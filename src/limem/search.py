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

        # Cache for entity embeddings to avoid repeated API calls
        self._entity_embedding_cache: dict[str, list[float]] = {}
        # Cache for database entity embeddings
        self._db_entity_embeddings_cache: Optional[dict[str, list[float]]] = None
        self._db_entity_cache_time: float = 0
        self._db_entity_cache_ttl: float = 60.0  # Cache TTL in seconds

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
        """Get embedding vector for an entity with caching.

        Args:
            entity: Entity name string.

        Returns:
            Embedding vector or None if generation fails.
        """
        # Check cache first
        if entity in self._entity_embedding_cache:
            return self._entity_embedding_cache[entity]

        try:
            resp = TextEmbedding.call(model=EMBEDDING_MODEL, input=entity)
            output = resp.output
            if isinstance(output, dict):
                embedding = output["embeddings"][0]["embedding"]
            else:
                embedding = output.embeddings[0].embedding

            # Cache the result
            self._entity_embedding_cache[entity] = embedding
            return embedding
        except Exception as ex:
            print(f"⚠️ Failed to generate embedding for '{entity}': {ex}")
            return None

    def _batch_get_entity_embeddings(self, entities: list[str]) -> dict[str, list[float]]:
        """Get embeddings for multiple entities in a batch API call.

        Args:
            entities: List of entity name strings.

        Returns:
            Dictionary mapping entity names to their embeddings.
        """
        if not entities:
            return {}

        # Separate cached and uncached entities
        cached = {}
        uncached = []
        for entity in entities:
            if entity in self._entity_embedding_cache:
                cached[entity] = self._entity_embedding_cache[entity]
            else:
                uncached.append(entity)

        if not uncached:
            return cached

        # Batch call for uncached entities
        try:
            resp = TextEmbedding.call(model=EMBEDDING_MODEL, input=uncached)
            output = resp.output

            if isinstance(output, dict):
                embeddings_list = output["embeddings"]
            else:
                embeddings_list = output.embeddings

            # Map embeddings to entities and cache them
            for i, entity in enumerate(uncached):
                if i < len(embeddings_list):
                    embedding = embeddings_list[i]["embedding"] if isinstance(embeddings_list[i], dict) else embeddings_list[i].embedding
                    cached[entity] = embedding
                    self._entity_embedding_cache[entity] = embedding

            print(f"⚡ Batch generated {len(uncached)} entity embeddings")
        except Exception as ex:
            print(f"⚠️ Failed to batch generate embeddings: {ex}")
            # Fallback to individual calls for uncached entities
            for entity in uncached:
                emb = self._get_entity_embedding(entity)
                if emb:
                    cached[entity] = emb

        return cached

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
        """Find similar entities using vector similarity with caching.

        For each input entity, generates an embedding and finds
        similar entities in the database based on cosine similarity.

        Args:
            entities: List of extracted entity names.

        Returns:
            Dictionary mapping matched entity IDs to their best similarity score.
        """
        if not entities or not self.config.enable_vector_match:
            return {}

        import time
        current_time = time.time()

        # Use cached database entity embeddings if available and not expired
        if (self._db_entity_embeddings_cache is not None and
            current_time - self._db_entity_cache_time < self._db_entity_cache_ttl):
            entity_embeddings = self._db_entity_embeddings_cache
        else:
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

            # Update cache
            self._db_entity_embeddings_cache = entity_embeddings
            self._db_entity_cache_time = current_time

        if not entity_embeddings:
            print("ℹ️ No entities with embeddings found in database")
            return {}

        # Batch generate embeddings for all query entities
        query_embeddings = self._batch_get_entity_embeddings(entities)

        # Match each extracted entity via vector similarity
        matched_entities = {}
        for entity, query_emb in query_embeddings.items():
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

    # ========================
    # Retrieval Methods (Abstraction)
    # ========================

    def _exact_match_search(self, entities: list[str]) -> dict[str, dict[str, Any]]:
        """Perform exact and containment matching to retrieve events.

        This is the precise retrieval path that prioritizes accuracy:
        1. Exact string match: query entity == db entity (weight = 1.0)
        2. Containment match: query entity in db entity (weight = 0.9)
           e.g., "医院" matches "北京协和医院"

        Args:
            entities: List of extracted entity names from query.

        Returns:
            Dictionary mapping event_id to event data with entity_match_weights.
        """
        if not entities:
            return {}

        events = {}

        # Step 1: Exact string match (weight = 1.0)
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
            print(f"⚠️ Exact match query failed: {ex}")
            return {}

        while resp.has_next():
            row = resp.get_next()
            event_id = row[0]
            entity_id = row[13]

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
                    "match_type": "exact",
                }

            events[event_id]["entity_match_weights"][entity_id] = 1.0

        exact_count = len(events)
        print(f"🎯 Exact matched {exact_count} events")

        # Step 2: Containment match (weight = 0.9)
        # Find DB entities that contain query entities
        try:
            resp = self.conn.execute("MATCH (en:Entity) RETURN en.id")
            db_entities = []
            while resp.has_next():
                db_entities.append(resp.get_next()[0])
        except Exception as ex:
            print(f"⚠️ Failed to fetch entities for containment: {ex}")
            db_entities = []

        containment_matched_entities = {}
        for qe in entities:
            if len(qe) < 2:  # Skip single chars
                continue
            for de in db_entities:
                if qe in de and qe != de:
                    containment_matched_entities[de] = 0.9

        if containment_matched_entities:
            containment_entity_ids = list(containment_matched_entities.keys())
            query = """
            MATCH (e:Event)-[r:INVOLVES]->(en:Entity)
            WHERE en.id IN $entity_list
            RETURN e.id, e.summary, e.action, e.causality, e.participants,
                   e.location, e.time_range, e.last_active,
                   r.t_created, r.t_expired, r.t_valid, r.t_invalid, r.c_valid, en.id
            """

            try:
                resp = self.conn.execute(query, {"entity_list": containment_entity_ids})
            except Exception as ex:
                print(f"⚠️ Containment match query failed: {ex}")
            else:
                new_events_from_containment = 0
                while resp.has_next():
                    row = resp.get_next()
                    event_id = row[0]
                    entity_id = row[13]
                    weight = containment_matched_entities[entity_id]

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
                            "match_type": "containment",
                        }
                        new_events_from_containment += 1

                    # Set weight if not already set by exact match
                    if entity_id not in events[event_id]["entity_match_weights"]:
                        events[event_id]["entity_match_weights"][entity_id] = weight

                if new_events_from_containment > 0:
                    print(f"🔗 Containment matched {new_events_from_containment} additional events")
                    # Show which entities were matched
                    matched_names = [k for k in containment_matched_entities.keys()][:3]
                    print(f"   - Matched entities: {matched_names}")

        return events

    def _fuzzy_match_search(self, entities: list[str]) -> dict[str, dict[str, Any]]:
        """Perform fuzzy/semantic matching to retrieve events.

        This is the approximate retrieval path using vector similarity:
        - Uses embedding vectors to find semantically similar entities
        - Weight = similarity² (squared to amplify differences)

        Args:
            entities: List of extracted entity names from query.

        Returns:
            Dictionary mapping event_id to event data with entity_match_weights.
        """
        if not entities or not self.config.enable_vector_match:
            return {}

        events = {}

        # Get vector-matched entities
        vector_matched_entities = self._vector_match_entities(entities)
        if not vector_matched_entities:
            return {}

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
            print(f"⚠️ Fuzzy match query failed: {ex}")
            return {}

        while resp.has_next():
            row = resp.get_next()
            event_id = row[0]
            entity_id = row[13]
            similarity = vector_matched_entities[entity_id]
            # Squared similarity to amplify differences
            weight = similarity ** 2

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
                    "match_type": "fuzzy",
                }

            # Set weight if not already set
            if entity_id not in events[event_id]["entity_match_weights"]:
                events[event_id]["entity_match_weights"][entity_id] = weight

        print(f"🔮 Fuzzy matched {len(events)} events")
        return events

    def fetch_weighted_events(
        self, entities: list[str]
    ) -> list[dict[str, Any]]:
        """Fetch events using hybrid retrieval: exact match first, then fuzzy match.

        Combines results from both retrieval paths with weighted fusion:
        1. Exact match search (exact string + containment) - high precision
        2. Fuzzy match search (vector semantic) - high recall

        Events from exact match are prioritized; fuzzy match fills in gaps.

        Args:
            entities: List of entity names to search for.

        Returns:
            List of event dictionaries with relationship attributes and
            entity match weights.
        """
        if not entities:
            return []

        # Step 1: Exact match search (precise retrieval)
        exact_events = self._exact_match_search(entities)

        # Step 2: Fuzzy match search (semantic retrieval)
        fuzzy_events = self._fuzzy_match_search(entities)

        # Step 3: Merge results - exact takes priority
        # Events from exact match keep their high weights
        # Events only from fuzzy match are added with lower priority
        merged_events = dict(exact_events)  # Start with exact matches

        for event_id, event_data in fuzzy_events.items():
            if event_id not in merged_events:
                # New event from fuzzy match
                merged_events[event_id] = event_data
            else:
                # Event exists from exact match - update match type
                merged_events[event_id]["match_type"] = "exact+fuzzy"
                # Merge entity match weights (keep higher weights)
                for entity_id, weight in event_data["entity_match_weights"].items():
                    if entity_id not in merged_events[event_id]["entity_match_weights"]:
                        merged_events[event_id]["entity_match_weights"][entity_id] = weight

        print(f"📊 Total: {len(merged_events)} events (exact: {len(exact_events)}, fuzzy-only: {len(merged_events) - len(exact_events)})")
        return list(merged_events.values())

    # ========================
    # Stage 3: Weight-based Reranking
    # ========================

    def _calculate_weight(self, row: dict[str, Any], t_now: int) -> float:
        """Calculate weight for an event using the decay formula.

        Formula: w_ij = log(1 + c_valid) * exp(-DECAY_RATE * (t_now - t_valid)) * entity_match_factor

        The entity_match_factor calculation is stratified by match type:
        - Exact match (weight >= 1.0): High precision, use max weight as base
        - Containment match (weight >= 0.9): High precision, use max weight as base
        - Fuzzy match (weight < 0.9): Low precision, use sum of weights

        This ensures that precise matches are not drowned out by many fuzzy matches.

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
        from .config import DECAY_RATE
        decay_rate = DECAY_RATE

        # Base weight: frequency reinforcement
        base_weight = math.log(1 + c_valid)

        # Temporal decay: exponential decay over time
        temporal_factor = math.exp(-decay_rate * time_diff)

        # Combined weight
        weight = base_weight * temporal_factor

        # Calculate entity match factor with stratified weighting
        entity_match_weights = row.get("entity_match_weights", {})
        if entity_match_weights:
            # Separate precise matches from fuzzy matches
            precise_weights = [w for w in entity_match_weights.values() if w >= 0.9]
            fuzzy_weights = [w for w in entity_match_weights.values() if w < 0.9]

            if precise_weights:
                # Precise match (exact or containment): Use max weight * multiplier
                # This ensures precise matches are not drowned out by fuzzy matches
                max_precise = max(precise_weights)
                precise_count = len(precise_weights)
                # Base factor from precise match, with bonus for multiple precise matches
                entity_match_factor = max_precise * (1 + 0.5 * (precise_count - 1))
            else:
                # Only fuzzy matches: Use sum of weights (lower priority)
                # Apply a discount factor to fuzzy-only matches
                entity_match_factor = sum(fuzzy_weights) * 0.5

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
                    match_type=event.get("match_type", ""),
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
