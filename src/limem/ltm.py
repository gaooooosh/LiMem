# -*- coding: utf-8 -*-
import json
import math
import uuid

import dashscope
from dashscope import Generation, TextEmbedding

from .config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    DECAY_RATE,
    DEFAULT_USER_ID,
    ENABLE_THINKING,
    EMBEDDING_MODEL,
    EPISODE_TTL,
    GENERATION_MODEL,
    PRUNE_C_VALID_THRESHOLD,
    PRUNE_EVIDENCE_TOP_K,
    SIMILARITY_THRESHOLD,
)
from .models import EpisodicEventFrame
from .utils import (
    hash_summary,
    load_prompt,
    normalize_event_payload,
    robust_json_loads,
    safe_json_dumps,
    safe_json_loads,
    time_bucket_from_ts,
)


class ResearchLTM:
    def __init__(self, conn):
        self.conn = conn
        dashscope.base_http_api_url = DASHSCOPE_BASE_URL
        if not DASHSCOPE_API_KEY or DASHSCOPE_API_KEY in {"YOUR_API_KEY", "sk-xxx"}:
            raise ValueError("Set DASHSCOPE_API_KEY in .env or environment.")
        dashscope.api_key = DASHSCOPE_API_KEY

    def get_embedding(self, text):
        # Retrieval embedding for the event summary (LLM -> vector).
        resp = TextEmbedding.call(model=EMBEDDING_MODEL, input=text)
        output = resp.output
        if isinstance(output, dict):
            return output["embeddings"][0]["embedding"]
        return output.embeddings[0].embedding

    def extract_event_from_llm(self, episode_text):
        # Step 1: Extract event information only (no entities)
        system_msg = load_prompt("extract_event_only_system.txt")
        user_msg = load_prompt("extract_event_only_user.txt").format(
            episode_text=episode_text
        )
        if ENABLE_THINKING:
            print("⚠️ enable_thinking requires stream call; ignoring in non-stream mode.")
        resp = Generation.call(
            api_key=dashscope.api_key,
            model=GENERATION_MODEL,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            result_format="message",
            enable_thinking=ENABLE_THINKING,
        )
        if resp.status_code != 200:
            print(f"⚠️ LLM call failed: status={resp.status_code}")
            print(f"⚠️ code={resp.code} message={resp.message}")
            raise ValueError("LLM call failed. Check model name and API key.")
        output = resp.output
        content = output.choices[0].message.content
        data = robust_json_loads(content, {})
        if not data or not isinstance(data, dict):
            raise ValueError(f"Failed to parse event data from LLM output: {content[:200]}")
        return normalize_event_payload(data, episode_text=episode_text)

    def extract_entities_from_llm(self, episode_text):
        # Step 2: Extract entities separately
        system_msg = load_prompt("extract_entities_only_system.txt")
        user_msg = load_prompt("extract_entities_only_user.txt").format(
            episode_text=episode_text
        )
        if ENABLE_THINKING:
            print("⚠️ enable_thinking requires stream call; ignoring in non-stream mode.")
        resp = Generation.call(
            api_key=dashscope.api_key,
            model=GENERATION_MODEL,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            result_format="message",
            enable_thinking=ENABLE_THINKING,
        )
        if resp.status_code != 200:
            print(f"⚠️ LLM call failed: status={resp.status_code}")
            print(f"⚠️ code={resp.code} message={resp.message}")
            raise ValueError("LLM call failed. Check model name and API key.")
        output = resp.output
        content = output.choices[0].message.content
        entities = robust_json_loads(content, [])
        # Ensure entities is a list
        if not isinstance(entities, list):
            entities = []
        return entities

    def calculate_weight(self, last_active, c_valid, t_now, t_expired, t_invalid):
        # Frequency reinforcement * temporal decay with invalidation masking.
        if t_expired is not None:
            return 0.0
        if t_invalid is not None and t_now >= t_invalid:
            return 0.0
        if last_active is None:
            last_active = 0
        return math.log(1 + c_valid) * math.exp(-DECAY_RATE * (t_now - last_active))

    def _build_event_frame(self, extracted, episode_content, current_time):
        event_payload = extracted.get("event") or extracted
        frame = EpisodicEventFrame.from_partial(event_payload, current_time)
        if not frame.summary:
            frame.summary = episode_content[:120]
        if frame.time_range.get("start", 0) == 0:
            frame.time_range["start"] = current_time
        if frame.time_range.get("end", 0) == 0:
            frame.time_range["end"] = current_time
        if not frame.time_range.get("display_time_bucket", ""):
            frame.time_range["display_time_bucket"] = time_bucket_from_ts(current_time)
        frame.last_active = current_time
        return frame

    def _serialize_event_fields(self, frame):
        data = frame.to_db_fields()
        return {
            "summary": data["summary"],
            "participants": safe_json_dumps(data["participants"]),
            "time_range": safe_json_dumps(data["time_range"]),
            "location": safe_json_dumps(data["location"]),
            "action": data["action"],
            "causality": data["causality"],
            "evidence": safe_json_dumps(data["evidence"]),
            "consistency": data["consistency"],
            "last_active": data["last_active"],
        }

    def _merge_evidence(self, existing_raw, incoming):
        existing = safe_json_loads(existing_raw, [])
        merged = list(existing)
        merged.extend(incoming)
        return merged

    def _ensure_user(self, user_id):
        resp = self.conn.execute(
            "MATCH (u:User {id: $id}) RETURN count(*)",
            {"id": user_id},
        )
        exists = resp.has_next() and resp.get_next()[0] > 0
        if not exists:
            self.conn.execute("CREATE (:User {id: $id})", {"id": user_id})
        return user_id

    def _promote_permanent_trait(self, user_id, event_id, t_now):
        self._ensure_user(user_id)
        resp = self.conn.execute(
            """
            MATCH (u:User {id: $user_id})-[r:PERMANENT_TRAIT]->(e:Event {id: $event_id})
            RETURN count(*)
            """,
            {"user_id": user_id, "event_id": event_id},
        )
        exists = resp.has_next() and resp.get_next()[0] > 0
        if not exists:
            self.conn.execute(
                """
                MATCH (u:User {id: $user_id}), (e:Event {id: $event_id})
                CREATE (u)-[:PERMANENT_TRAIT {t_created: $t_created}]->(e)
                """,
                {"user_id": user_id, "event_id": event_id, "t_created": t_now},
            )

    def _prune_event_evidence(self, event_id):
        resp = self.conn.execute(
            "MATCH (e:Event {id: $id}) RETURN e.evidence",
            {"id": event_id},
        )
        if not resp.has_next():
            return
        evidence_raw = resp.get_next()[0]
        evidence = safe_json_loads(evidence_raw, [])
        if not evidence:
            return
        sorted_items = sorted(
            evidence,
            key=lambda item: float(item.get("confidence", 0.0)),
            reverse=True,
        )
        trimmed = sorted_items[:PRUNE_EVIDENCE_TOP_K]
        self.conn.execute(
            "MATCH (e:Event {id: $id}) SET e.evidence = $evidence",
            {"id": event_id, "evidence": safe_json_dumps(trimmed)},
        )

    def _ensure_entity(self, entity):
        # Entity is a symbol node; create if missing (simple existence check).
        entity_id = entity.get("name") if isinstance(entity, dict) else str(entity)
        if not entity_id:
            return None
        resp = self.conn.execute(
            "MATCH (e:Entity {id: $id}) RETURN count(*)",
            {"id": entity_id},
        )
        exists = resp.has_next() and resp.get_next()[0] > 0
        if not exists:
            # Generate embedding for the entity
            embedding = self.get_embedding(entity_id)
            self.conn.execute(
                "CREATE (:Entity {id: $id, type: $type, embedding: $embedding})",
                {"id": entity_id, "type": entity.get("type", "UNKNOWN"), "embedding": embedding},
            )
        return entity_id

    def _find_most_similar_event(self, embedding, current_time=None, incoming_entities=None, incoming_action=None):
        """
        Multi-dimensional event similarity search.

        Combines:
        1. Semantic similarity (vector embeddings)
        2. Entity overlap (Jaccard similarity)
        3. Temporal proximity (time decay)
        4. Action type matching (exact match boost)

        Returns: (best_id, best_summary, combined_score, debug_info)
        """
        from .config import (
            MERGE_WEIGHT_SEMANTIC,
            MERGE_WEIGHT_ENTITY,
            MERGE_WEIGHT_TIME,
            MERGE_WEIGHT_ACTION,
            MERGE_TIME_WINDOW,
        )

        resp = self.conn.execute("""
            MATCH (e:Event)-[r:INVOLVES]->(en:Entity)
            RETURN e.id, e.summary, e.embedding, e.action, e.last_active, collect(en.id)
        """)

        best_id = None
        best_summary = None
        best_combined_score = None
        best_debug = None

        while resp.has_next():
            event_id, summary, stored_emb, stored_action, last_active, stored_entities = resp.get_next()

            # 1. Semantic similarity (0-1)
            semantic_sim = self._cosine_similarity(embedding, stored_emb)

            # 2. Entity overlap (Jaccard similarity, 0-1)
            entity_sim = 0.0
            if incoming_entities and stored_entities:
                incoming_set = set(e.get("name", e) if isinstance(e, dict) else e for e in incoming_entities)
                stored_set = set(stored_entities)
                if incoming_set or stored_set:
                    intersection = len(incoming_set & stored_set)
                    union = len(incoming_set | stored_set)
                    entity_sim = intersection / union if union > 0 else 0.0

            # 3. Temporal proximity (0-1, exponential decay)
            time_sim = 0.0
            if current_time is not None and last_active is not None:
                time_diff = abs(current_time - last_active)
                if time_diff <= MERGE_TIME_WINDOW:
                    # Exponential decay: full score at t=0, ~0.37 at t=MERGE_TIME_WINDOW
                    time_sim = math.exp(-3.0 * time_diff / MERGE_TIME_WINDOW)
                else:
                    time_sim = 0.0
            elif current_time is None:
                # If no time info, don't penalize
                time_sim = 0.5

            # 4. Action type matching (0 or 1)
            action_sim = 0.0
            if incoming_action and stored_action:
                action_sim = 1.0 if incoming_action == stored_action else 0.0

            # Weighted combination
            combined_score = (
                MERGE_WEIGHT_SEMANTIC * semantic_sim +
                MERGE_WEIGHT_ENTITY * entity_sim +
                MERGE_WEIGHT_TIME * time_sim +
                MERGE_WEIGHT_ACTION * action_sim
            )

            # Debug info
            debug_info = {
                "semantic": semantic_sim,
                "entity": entity_sim,
                "time": time_sim,
                "action": action_sim,
                "combined": combined_score,
            }

            if best_combined_score is None or combined_score > best_combined_score:
                best_id = event_id
                best_summary = summary
                best_combined_score = combined_score
                best_debug = debug_info

        return best_id, best_summary, best_combined_score, best_debug

    def _cosine_similarity(self, vec_a, vec_b):
        # Simple cosine similarity for small-scale research runs.
        if not vec_a or not vec_b:
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

    def process_new_episode(self, episode_content, current_time):
        # === 1) Save raw Episode (episodic memory buffer) ===
        episode_id = uuid.uuid4().hex
        self.conn.execute(
            "CREATE (:Episode {id: $id, content: $content, timestamp: $ts})",
            {"id": episode_id, "content": episode_content, "ts": current_time},
        )
        print(f"📝 Saved Episode: {episode_id} at t={current_time}")

        # === 2) Extract Event (LLM abstraction - Step 1) ===
        event_data = self.extract_event_from_llm(episode_content)
        frame = self._build_event_frame(event_data, episode_content, current_time)
        summary = frame.summary
        print(f"🧠 Extracted Event: {summary}")

        # === 3) Extract Entities (LLM abstraction - Step 2) ===
        entities = self.extract_entities_from_llm(episode_content)
        print(f"🧩 Entities: {entities}")

        # === 4) Recall: multi-dimensional similarity search ===
        embedding = self.get_embedding(summary)
        best_id, best_summary, combined_score, debug_info = self._find_most_similar_event(
            embedding,
            current_time=current_time,
            incoming_entities=entities,
            incoming_action=frame.action
        )
        if combined_score is not None:
            print(f"🔎 Top match (combined={combined_score:.4f}): {best_summary}")
            if debug_info:
                print(f"   └─ semantic={debug_info['semantic']:.3f}, "
                      f"entity={debug_info['entity']:.3f}, "
                      f"time={debug_info['time']:.3f}, "
                      f"action={debug_info['action']:.3f}")

        # === 5) Consolidation: merge or create ===
        event_fields = self._serialize_event_fields(frame)
        incoming_evidence = frame.to_db_fields()["evidence"]
        if combined_score is not None and combined_score > SIMILARITY_THRESHOLD:
            print(f"🔍 Found existing memory: {best_summary}")
            event_id = best_id

            existing_resp = self.conn.execute(
                "MATCH (e:Event {id: $id}) RETURN e.evidence",
                {"id": event_id},
            )
            existing_evidence_raw = None
            if existing_resp.has_next():
                existing_evidence_raw = existing_resp.get_next()[0]
            merged_evidence = self._merge_evidence(existing_evidence_raw, incoming_evidence)

            # Refresh embedding and last_active for the existing event node.
            self.conn.execute(
                """
                MATCH (e:Event {id: $id})
                SET e.embedding = $embedding,
                    e.last_active = $last_active,
                    e.evidence = $evidence
                """,
                {
                    "id": event_id,
                    "embedding": embedding,
                    "last_active": event_fields["last_active"],
                    "evidence": safe_json_dumps(merged_evidence),
                },
            )

        else:
            print("🆕 Creating new memory...")
            event_id = hash_summary(summary)
            self.conn.execute(
                """
                CREATE (:Event {
                    id: $id,
                    summary: $summary,
                    participants: $participants,
                    time_range: $time_range,
                    location: $location,
                    action: $action,
                    causality: $causality,
                    evidence: $evidence,
                    consistency: $consistency,
                    last_active: $last_active,
                    embedding: $embedding
                })
                """,
                {
                    "id": event_id,
                    "embedding": embedding,
                    **event_fields,
                },
            )

        # === 6) Link Event -> Episode (provenance) ===
        self.conn.execute(
            """
            MATCH (e:Event {id: $event_id}), (ep:Episode {id: $episode_id})
            CREATE (e)-[:EXTRACTED_FROM]->(ep)
            """,
            {"event_id": event_id, "episode_id": episode_id},
        )

        # === 7) Update INVOLVES edges (core memory strength) ===
        for entity in entities:
            entity_id = self._ensure_entity(entity)
            if not entity_id:
                continue
            rel_resp = self.conn.execute(
                """
                MATCH (e:Event {id: $event_id})-[r:INVOLVES]->(en:Entity {id: $entity_id})
                RETURN r.t_created, r.t_expired, r.t_valid, r.t_invalid, r.c_valid
                """,
                {"event_id": event_id, "entity_id": entity_id},
            )
            if rel_resp.has_next():
                row = rel_resp.get_next()
                c_valid_new = (row[4] or 0) + 1
                self.conn.execute(
                    """
                    MATCH (e:Event {id: $event_id})-[r:INVOLVES]->(en:Entity {id: $entity_id})
                    SET r.t_valid = $t_valid,
                        r.c_valid = $c_valid
                    """,
                    {
                        "event_id": event_id,
                        "entity_id": entity_id,
                        "t_valid": current_time,
                        "c_valid": c_valid_new,
                    },
                )
            else:
                self.conn.execute(
                    """
                    MATCH (e:Event {id: $event_id}), (en:Entity {id: $entity_id})
                    CREATE (e)-[:INVOLVES {
                        t_created: $t_created,
                        t_expired: $t_expired,
                        t_valid: $t_valid,
                        t_invalid: $t_invalid,
                        c_valid: $c_valid
                    }]->(en)
                    """,
                    {
                        "event_id": event_id,
                        "entity_id": entity_id,
                        "t_created": current_time,
                        "t_expired": None,
                        "t_valid": current_time,
                        "t_invalid": None,
                        "c_valid": 1,
                    },
                )
                c_valid_new = 1

            if c_valid_new > PRUNE_C_VALID_THRESHOLD:
                self._prune_event_evidence(event_id)
                self._promote_permanent_trait(DEFAULT_USER_ID, event_id, current_time)

        return event_id

    def peek_decayed_weights(self, event_id, current_time):
        # Observation-only query: compute decayed weight without updating DB.
        resp = self.conn.execute(
            """
            MATCH (e:Event {id: $event_id})-[r:INVOLVES]->(en:Entity)
            RETURN e.summary, en.id, e.last_active, r.c_valid, r.t_expired, r.t_invalid
            """,
            {"event_id": event_id},
        )
        while resp.has_next():
            row = resp.get_next()
            summary, entity_id, last_active, c_valid, t_expired, t_invalid = row
            decayed = self.calculate_weight(
                last_active, c_valid or 0, current_time, t_expired, t_invalid
            )
            print(
                f"📉 Decayed weight @t={current_time} | {summary} -> {entity_id} | "
                f"decayed={decayed:.4f}"
            )

    def cleanup_ttl(self, current_time):
        # Episodic memory cleanup: delete old Episode nodes only.
        threshold = current_time - EPISODE_TTL
        count_resp = self.conn.execute(
            "MATCH (e:Episode) WHERE e.timestamp < $threshold RETURN count(*)",
            {"threshold": threshold},
        )
        count = count_resp.get_next()[0] if count_resp.has_next() else 0
        self.conn.execute(
            "MATCH (e:Episode) WHERE e.timestamp < $threshold DETACH DELETE e",
            {"threshold": threshold},
        )
        print(f"🗑️ Cleaned up {count} old episodes")
