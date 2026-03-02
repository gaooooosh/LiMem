# -*- coding: utf-8 -*-
"""LTM Search and Retrieval Pipeline.

Implements the four-stage retrieval algorithm:
1. Entity Extraction (LLM)
2. Graph Path Search (Kuzu Cypher)
3. Weight-based Reranking
4. LLM Summarization
"""

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import dashscope
from dashscope import Generation, TextEmbedding

from .config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    DECAY_RATE,
    EMBEDDING_MODEL,
    ENABLE_THINKING,
    GENERATION_MODEL,
    SEARCH_ENABLE_VECTOR_MATCH,
    SEARCH_LAMBDA,
    SEARCH_MAX_ENTITIES,
    SEARCH_MAX_TOKENS,
    SEARCH_TEMPERATURE,
    SEARCH_TOP_K,
    SEARCH_VECTOR_THRESHOLD,
    SEARCH_VECTOR_TOP_K,
)
from .models import EpisodicEventFrame, RankedEvent
from .utils import load_prompt


@dataclass
class RetrievalConfig:
    """Configuration for the retrieval pipeline.

    All defaults are read from environment variables defined in config.py.
    """

    # Top-K selection
    default_top_k: int = SEARCH_TOP_K

    # Weight calculation
    lambda_param: float = SEARCH_LAMBDA

    # Entity extraction
    max_entities: int = SEARCH_MAX_ENTITIES

    # LLM generation
    max_tokens: int = SEARCH_MAX_TOKENS
    temperature: float = SEARCH_TEMPERATURE

    # Hybrid entity matching (exact + vector)
    enable_vector_match: bool = SEARCH_ENABLE_VECTOR_MATCH
    vector_similarity_threshold: float = SEARCH_VECTOR_THRESHOLD
    vector_match_top_k: int = SEARCH_VECTOR_TOP_K


class LTMSearcher:
    """Long-Term Memory Search and Retrieval System.

    Implements the complete retrieval pipeline with entity extraction,
    graph traversal, weighted reranking, and semantic summarization.
    """

    def __init__(self, conn, config: Optional[RetrievalConfig] = None):
        """Initialize the LTM searcher.

        Args:
            conn: Kuzu database connection.
            config: Optional retrieval configuration.
        """
        self.conn = conn
        self.config = config or RetrievalConfig()

        # Initialize Dashscope
        dashscope.base_http_api_url = DASHSCOPE_BASE_URL
        if not DASHSCOPE_API_KEY or DASHSCOPE_API_KEY in {"YOUR_API_KEY", "sk-xxx"}:
            raise ValueError("Set DASHSCOPE_API_KEY in .env or environment.")
        dashscope.api_key = DASHSCOPE_API_KEY

    # ========================
    # Stage 1: Entity Extraction
    # ========================

    def extract_entities(self, query: str) -> list[str]:
        """Extract core entities from natural language query.

        Uses LLM to identify entities like people, preferences,
        media types, and actions.

        Args:
            query: Natural language query string.

        Returns:
            List of extracted entity names.
        """
        system_msg = load_prompt("entity_extraction_system.txt")
        user_msg = load_prompt("entity_extraction_user.txt").format(query=query)

        resp = Generation.call(
            api_key=dashscope.api_key,
            model=GENERATION_MODEL,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            result_format="message",
            enable_thinking=False,
        )

        if resp.status_code != 200:
            print(f"⚠️ Entity extraction failed: {resp.message}")
            return []

        content = resp.output.choices[0].message.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        try:
            entities = json.loads(content)
            if isinstance(entities, list):
                # Filter out meaningless single characters and common stop words
                # Keep single chars only if they are meaningful (like numbers, common nouns)
                stop_words = {
                    'w', 'sha', 'a', 'an', '的', '了', '吗', '呢', '啊',
                    '是', '在', '有', '和', '与', '或', '但', '而', '如', '让', '给', '把', '被'
                }

                valid_entities = []
                for e in entities:
                    e_str = str(e).strip()
                    if not e_str or e_str in stop_words:
                        continue

                    # Filter out single characters unless they are meaningful
                    # Keep: numbers, English letters, common meaningful single chars
                    if len(e_str) == 1:
                        # Keep if it's a number, English letter, or specific meaningful chars
                        if e_str.isdigit() or e_str.isalpha() and e_str.isascii():
                            # Keep single digits/letters (like "25", "K")
                            valid_entities.append(e_str)
                        # Otherwise filter out single Chinese characters
                        # (unless they are specifically meaningful like '歌', '书', etc.)
                        elif e_str in {'歌', '书', '车', '家', '去', '听', '看', '放'}:
                            valid_entities.append(e_str)
                        # Skip all other single characters
                    else:
                        # Keep multi-character entities
                        valid_entities.append(e_str)

                # Deduplicate while preserving order
                seen = set()
                unique_entities = []
                for entity in valid_entities:
                    if entity not in seen:
                        seen.add(entity)
                        unique_entities.append(entity)
                return unique_entities
            return [str(entities)]
        except json.JSONDecodeError:
            # Fallback: try to extract quoted strings
            import re

            quoted = re.findall(r'"([^"]+)"', content)
            return [q for q in quoted if len(q) >= 1]

    # ========================
    # Stage 2: Graph Path Search
    # ========================

    def _get_entity_embedding(self, entity: str) -> Optional[list[float]]:
        """Get embedding vector for an entity.

        Args:
            entity: Entity name string.

        Returns:
            Embedding vector or None if generation fails.
        """
        try:
            resp = TextEmbedding.call(model=EMBEDDING_MODEL, input=entity)
            output = resp.output
            if isinstance(output, dict):
                return output["embeddings"][0]["embedding"]
            return output.embeddings[0].embedding
        except Exception as ex:
            print(f"⚠️ Failed to generate embedding for '{entity}': {ex}")
            return None

    def _cosine_similarity(self, vec_a: list[float], vec_b: list[float]) -> float:
        """Calculate cosine similarity between two vectors.

        Args:
            vec_a: First vector.
            vec_b: Second vector.

        Returns:
            Cosine similarity score between -1 and 1.
        """
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return -1.0
        dot = 0.0
        norm_a = 0.0
        norm_b = 0.0
        for a, b in zip(vec_a, vec_b):
            dot += a * b
            norm_a += a * a
            norm_b += b * b
        if norm_a == 0.0 or norm_b == 0.0:
            return -1.0
        return dot / math.sqrt(norm_a * norm_b)

    def _vector_match_entities(
        self, entities: list[str]
    ) -> dict[str, float]:
        """Find similar entities using vector similarity.

        For each input entity, generates an embedding and finds
        similar entities in the database based on cosine similarity.

        Args:
            entities: List of extracted entity names.

        Returns:
            Dictionary mapping matched entity IDs to their best similarity score.
        """
        if not entities or not self.config.enable_vector_match:
            return {}

        # Fetch all entities with embeddings from database
        try:
            resp = self.conn.execute("MATCH (en:Entity) RETURN en.id, en.embedding")
        except Exception as ex:
            print(f"⚠️ Failed to fetch entities: {ex}")
            return {}

        # Build entity embedding map
        entity_embeddings = {}
        while resp.has_next():
            row = resp.get_next()
            entity_id, embedding = row[0], row[1]
            if embedding:  # Only include entities with embeddings
                entity_embeddings[entity_id] = embedding

        if not entity_embeddings:
            print("ℹ️ No entities with embeddings found in database")
            return {}

        # Match each extracted entity via vector similarity
        matched_entities = {}
        for entity in entities:
            query_emb = self._get_entity_embedding(entity)
            if not query_emb:
                continue

            # Calculate similarity with all entities
            similarities = []
            for db_entity_id, db_emb in entity_embeddings.items():
                sim = self._cosine_similarity(query_emb, db_emb)
                if sim >= self.config.vector_similarity_threshold:
                    similarities.append((db_entity_id, sim))

            # Keep top-K matches
            similarities.sort(key=lambda x: x[1], reverse=True)
            for entity_id, sim in similarities[: self.config.vector_match_top_k]:
                # Keep the best similarity score for each entity
                if entity_id not in matched_entities or sim > matched_entities[entity_id]:
                    matched_entities[entity_id] = sim

        if matched_entities:
            print(f"🎯 Vector matched {len(matched_entities)} entities")
            for eid, sim in sorted(matched_entities.items(), key=lambda x: -x[1])[:3]:
                print(f"   - {eid}: {sim:.4f}")

        return matched_entities

    def fetch_weighted_events(
        self, entities: list[str]
    ) -> list[dict[str, Any]]:
        """Fetch events connected to the given entities.

        Performs hybrid entity matching combining:
        1. Exact string matching (via Cypher)
        2. Vector similarity matching (semantic)

        Args:
            entities: List of entity names to search for.

        Returns:
            List of event dictionaries with relationship attributes and
            entity match weights.
        """
        if not entities:
            return []

        # === Step 1: Exact match ===
        query = """
        MATCH (e:Event)-[r:INVOLVES]->(en:Entity)
        WHERE en.id IN $entity_list
        RETURN e.id, e.summary, e.action, e.causality, e.participants,
               e.location, e.time_range, e.last_active,
               r.t_created, r.t_expired, r.t_valid, r.t_invalid, r.c_valid, en.id
        """

        try:
            resp = self.conn.execute(query, {"entity_list": entities})
        except Exception as ex:
            print(f"⚠️ Graph query failed: {ex}")
            return []

        # Store event-entity relationships with exact match weights
        events = {}
        while resp.has_next():
            row = resp.get_next()
            event_id = row[0]
            entity_id = row[13]  # en.id

            if event_id not in events:
                events[event_id] = {
                    "event_id": event_id,
                    "summary": row[1],
                    "action": row[2] or "",
                    "causality": row[3] or "",
                    "participants": row[4] or "",
                    "location": row[5] or "",
                    "time_range": row[6] or "",
                    "last_active": row[7] or 0,
                    "t_created": row[8],
                    "t_expired": row[9],
                    "t_valid": row[10] or 0,
                    "t_invalid": row[11],
                    "c_valid": row[12] or 0,
                    "entity_match_weights": {},  # Map entity_id -> match_weight
                }

            # Exact match has weight 1.0
            events[event_id]["entity_match_weights"][entity_id] = 1.0
            events[event_id]["match_type"] = events[event_id].get("match_type", "exact")

        print(f"🎯 Exact matched {len(events)} events")

        # === Step 2: Vector match (semantic) ===
        vector_matched_entities = self._vector_match_entities(entities)
        if vector_matched_entities:
            # Query events for vector-matched entities
            vector_entity_ids = list(vector_matched_entities.keys())
            query = """
            MATCH (e:Event)-[r:INVOLVES]->(en:Entity)
            WHERE en.id IN $entity_list
            RETURN e.id, e.summary, e.action, e.causality, e.participants,
                   e.location, e.time_range, e.last_active,
                   r.t_created, r.t_expired, r.t_valid, r.t_invalid, r.c_valid, en.id
            """

            try:
                resp = self.conn.execute(query, {"entity_list": vector_entity_ids})
            except Exception as ex:
                print(f"⚠️ Vector match query failed: {ex}")
                return list(events.values())

            while resp.has_next():
                row = resp.get_next()
                event_id = row[0]
                entity_id = row[13]  # en.id
                similarity = vector_matched_entities[entity_id]**2

                # Add if not already present, or mark as both matches
                if event_id not in events:
                    events[event_id] = {
                        "event_id": event_id,
                        "summary": row[1],
                        "action": row[2] or "",
                        "causality": row[3] or "",
                        "participants": row[4] or "",
                        "location": row[5] or "",
                        "time_range": row[6] or "",
                        "last_active": row[7] or 0,
                        "t_created": row[8],
                        "t_expired": row[9],
                        "t_valid": row[10] or 0,
                        "t_invalid": row[11],
                        "c_valid": row[12] or 0,
                        "entity_match_weights": {},
                        "match_type": "vector",
                    }
                else:
                    events[event_id]["match_type"] = "both"

                # Vector match weight is the similarity score
                # If already has exact match (1.0), keep it; otherwise use similarity
                if entity_id not in events[event_id]["entity_match_weights"]:
                    events[event_id]["entity_match_weights"][entity_id] = similarity

        return list(events.values())

    # ========================
    # Stage 3: Weight-based Reranking
    # ========================

    def _calculate_weight(self, row: dict[str, Any], t_now: int) -> float:
        """Calculate weight for an event using the decay formula.

        Formula: w_ij = log(1 + c_valid) * exp(-DECAY_RATE * (t_now - t_valid)) * entity_match_factor

        The entity_match_factor is the product of all entity match weights,
        which boosts events connected to more precisely matched entities.

        Hard filter conditions:
        - If t_expired is not None, weight = 0
        - If t_invalid is not None and t_now >= t_invalid, weight = 0
        - If t_valid > t_now (future event), weight = 0

        Args:
            row: Event data dictionary with relationship attributes and
                 entity_match_weights mapping.
            t_now: Current Unix timestamp.

        Returns:
            Calculated weight score.
        """
        # Hard filter: expired events
        if row.get("t_expired") is not None:
            return 0.0

        # Hard filter: invalid events
        t_invalid = row.get("t_invalid")
        if t_invalid is not None and t_now >= t_invalid:
            return 0.0

        # Get c_valid (validation count)
        c_valid = row.get("c_valid", 0) or 0
        t_valid = row.get("t_valid", 0) or 0

        # Calculate time difference (FIX: use linear time, not logarithmic)
        time_diff = t_now - t_valid

        # Filter out future events (shouldn't happen in practice)
        if time_diff < 0:
            return 0.0

        # Calculate weight using proper exponential decay formula
        # FIX: Use DECAY_RATE from config, not lambda_param
        # FIX: Use linear time_diff, not log(1 + abs(time_diff))
        from .config import DECAY_RATE
        decay_rate = DECAY_RATE

        # Base weight: frequency reinforcement
        base_weight = math.log(1 + c_valid)

        # Temporal decay: exponential decay over time
        temporal_factor = math.exp(-decay_rate * time_diff)

        # Combined weight
        weight = base_weight * temporal_factor

        # Multiply by entity match weights
        entity_match_weights = row.get("entity_match_weights", {})
        if entity_match_weights:
            # FIX: Amplify entity match factor to prioritize semantic relevance over time
            # This ensures semantically relevant events rank higher even if they're older
            # Strategy: Use squared sum to amplify the impact of entity matches
            entity_match_factor = sum(entity_match_weights.values()) ** 2
            weight *= entity_match_factor

        return weight

    def rerank_events(
        self, raw_events: list[dict[str, Any]], top_k: Optional[int] = None
    ) -> tuple[list[RankedEvent], list[dict]]:
        """Rerank events by weight and return top-K with debug info.

        Args:
            raw_events: List of event dictionaries from graph query.
            top_k: Number of top events to return (default from config).

        Returns:
            Tuple of (List of RankedEvent objects sorted by weight (descending),
                     List of debug dictionaries with weight calculation details).
        """
        if not raw_events:
            return [], []

        # FIX: Use actual current time instead of max(t_valid)
        # This properly maintains temporal discrimination
        import time
        t_now = int(time.time())

        k = top_k or self.config.default_top_k

        # Calculate weights and create RankedEvent objects
        ranked = []
        debug_list = []
        for event in raw_events:
            weight = self._calculate_weight(event, t_now)
            if weight > 0:  # Only include non-zero weight events
                ranked_event = RankedEvent(
                    event_id=event["event_id"],
                    summary=event["summary"],
                    weight=weight,
                    c_valid=event.get("c_valid", 0) or 0,
                    t_valid=event.get("t_valid", 0) or 0,
                    t_expired=event.get("t_expired"),
                    t_invalid=event.get("t_invalid"),
                    action=event.get("action", ""),
                    causality=event.get("causality", ""),
                    participants=str(event.get("participants", "")),
                    location=str(event.get("location", "")),
                    time_range=str(event.get("time_range", "")),
                )
                ranked.append(ranked_event)

                # Create debug info for this event
                debug_info = {
                    "event_id": event["event_id"],
                    "summary": event["summary"],
                    "weight": weight,
                    "c_valid": event.get("c_valid", 0) or 0,
                    "t_valid": event.get("t_valid", 0) or 0,
                    "t_expired": event.get("t_expired"),
                    "t_invalid": event.get("t_invalid"),
                    "entity_match_weights": event.get("entity_match_weights", {}),
                    "match_type": event.get("match_type", ""),
                    "t_now": t_now,
                    "time_diff": t_now - (event.get("t_valid", 0) or 0),
                }
                debug_list.append(debug_info)

        # Sort by weight (descending)
        ranked.sort(key=lambda e: e.weight, reverse=True)
        debug_list.sort(key=lambda d: d["weight"], reverse=True)

        # Return top-K
        return ranked[:k], debug_list[:k]

    # ========================
    # Stage 4: LLM Summarization
    # ========================

    def generate_answer(
        self, query: str, events: list[RankedEvent]
    ) -> str:
        """Generate contextual answer based on retrieved events.

        Uses LLM to synthesize an answer that references the retrieved
        long-term memory events.

        Args:
            query: Original user query.
            events: List of top-K ranked events.

        Returns:
            Generated answer string.
        """
        if not events:
            return "抱歉，我没有找到相关的记忆来回答这个问题。"

        # Format events as context
        events_context = []
        for i, event in enumerate(events):
            event_info = f"事件 {i} (权重: {event.weight:.4f}, 确认次数: {event.c_valid}):\n"
            event_info += f"- 摘要: {event.summary}\n"
            if event.action:
                event_info += f"- 动作: {event.action}\n"
            if event.causality:
                event_info += f"- 因果: {event.causality}\n"
            if event.time_range:
                event_info += f"- 时间: {event.time_range}\n"

            events_context.append(event_info)

        events_str = "\n".join(events_context)

        system_msg = load_prompt("generate_answer_system.txt")

        user_msg = load_prompt("generate_answer_user.txt").format(
            events_context=events_str,
            query=query
        )
        resp = Generation.call(
            api_key=dashscope.api_key,
            model=GENERATION_MODEL,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            result_format="message",
            enable_thinking=False,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
        )

        if resp.status_code != 200:
            return f"抱歉，生成回答时遇到问题：{resp.message}"

        return resp.output.choices[0].message.content.strip()

    # ========================
    # Complete Retrieval Pipeline
    # ========================

    def search(self, query: str, top_k: Optional[int] = None) -> dict[str, Any]:
        """Execute the complete retrieval pipeline.

        Args:
            query: Natural language query.
            top_k: Number of top events to retrieve.

        Returns:
            Dictionary containing:
                - query: Original query
                - entities: Extracted entities
                - ranked_events: All ranked events
                - top_k_events: Top-K selected events
                - answer: Generated answer
        """
        # Stage 1: Entity Extraction
        entities = self.extract_entities(query)
        print(f"🧩 Extracted entities: {entities}")

        if not entities:
            return {
                "query": query,
                "entities": [],
                "ranked_events": [],
                "top_k_events": [],
                "answer": "抱歉，我无法从问题中提取到关键信息。",
            }

        # Stage 2: Graph Path Search
        raw_events = self.fetch_weighted_events(entities)
        print(f"🔍 Found {len(raw_events)} events from graph")

        # Stage 3: Weight-based Reranking
        ranked_events, _ = self.rerank_events(raw_events, top_k)
        print(f"📊 Ranked {len(ranked_events)} events by weight")

        # Stage 4: LLM Summarization
        answer = self.generate_answer(query, ranked_events)

        return {
            "query": query,
            "entities": entities,
            "ranked_events": ranked_events,
            "top_k_events": ranked_events[: (top_k or self.config.default_top_k)],
            "answer": answer,
        }

    def search_debug(
        self, query: str, top_k: Optional[int] = None
    ) -> dict[str, Any]:
        """Execute retrieval with detailed debugging output.

        Args:
            query: Natural language query.
            top_k: Number of top events to retrieve.

        Returns:
            Extended dictionary with debug information.
        """
        # Stage 1: Entity Extraction
        entities = self.extract_entities(query)
        print(f"🧩 Extracted entities: {entities}")

        if not entities:
            return {
                "query": query,
                "entities": [],
                "ranked_events": [],
                "top_k_events": [],
                "answer": "抱歉，我无法从问题中提取到关键信息。",
                "debug": {
                    "entity_count": 0,
                    "raw_event_count": 0,
                    "ranked_event_count": 0,
                    "top_k_count": 0,
                    "weight_calculation_details": [],
                },
            }

        # Stage 2: Graph Path Search
        raw_events = self.fetch_weighted_events(entities)
        print(f"🔍 Found {len(raw_events)} events from graph")

        # Stage 3: Weight-based Reranking
        ranked_events, debug_details = self.rerank_events(raw_events, top_k)
        print(f"📊 Ranked {len(ranked_events)} events by weight")

        # Stage 4: LLM Summarization
        answer = self.generate_answer(query, ranked_events)

        k = top_k or self.config.default_top_k

        # Add debug information
        result = {
            "query": query,
            "entities": entities,
            "ranked_events": ranked_events,
            "top_k_events": ranked_events[:k],
            "answer": answer,
            "debug": {
                "entity_count": len(entities),
                "raw_event_count": len(raw_events),
                "ranked_event_count": len(ranked_events),
                "top_k_count": len(ranked_events[:k]),
                "weight_calculation_details": debug_details,
            },
        }

        return result
