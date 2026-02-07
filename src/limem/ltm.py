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
    ENABLE_THINKING,
    EMBEDDING_MODEL,
    EPISODE_TTL,
    GENERATION_MODEL,
    SIMILARITY_THRESHOLD,
)
from .utils import hash_summary


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
        # Abstraction step: turn a raw episode into a single event + entities.
        system_msg = (
            "You are an information extraction module. "
            "Return ONLY JSON with keys: summary, entities. "
            'Each entity has: {"name": "...", "type": "..."}.'
        )
        user_msg = f"Text: {episode_text}\nOutput JSON:"
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
        raw = content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        data = json.loads(raw)
        return data

    def calculate_weight(self, t_last, c_valid, t_now):
        # Paper formula: frequency reinforcement * temporal decay.
        return math.log(1 + c_valid) * math.exp(-DECAY_RATE * (t_now - t_last))

    def _ensure_entity(self, entity):
        # Entity is a symbol node; create if missing (simple existence check).
        entity_id = entity["name"]
        resp = self.conn.execute(
            "MATCH (e:Entity {id: $id}) RETURN count(*)",
            {"id": entity_id},
        )
        exists = resp.has_next() and resp.get_next()[0] > 0
        if not exists:
            self.conn.execute(
                "CREATE (:Entity {id: $id, type: $type})",
                {"id": entity_id, "type": entity["type"]},
            )
        return entity_id

    def _find_most_similar_event(self, embedding):
        # Recall step: vector search over existing Event embeddings (Python cosine).
        resp = self.conn.execute("MATCH (e:Event) RETURN e.id, e.summary, e.embedding")
        best_id = None
        best_summary = None
        best_sim = None
        while resp.has_next():
            event_id, summary, stored_emb = resp.get_next()
            sim = self._cosine_similarity(embedding, stored_emb)
            if best_sim is None or sim > best_sim:
                best_id = event_id
                best_summary = summary
                best_sim = sim
        return best_id, best_summary, best_sim

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

        # === 2) Extract Event + Entities (LLM abstraction) ===
        extracted = self.extract_event_from_llm(episode_content)
        summary = extracted["summary"]
        entities = extracted.get("entities", [])
        print(f"🧠 Extracted Event: {summary}")
        print(f"🧩 Entities: {entities}")

        # === 3) Recall: embed + vector search ===
        embedding = self.get_embedding(summary)
        best_id, best_summary, sim = self._find_most_similar_event(embedding)
        if sim is not None:
            print(f"🔎 Top similarity: {sim:.4f} -> {best_summary}")

        # === 4) Consolidation: merge or create ===
        if sim is not None and sim > SIMILARITY_THRESHOLD:
            print(f"🔍 Found existing memory: {best_summary}")
            event_id = best_id

            # Refresh embedding for the existing event node.
            self.conn.execute(
                "MATCH (e:Event {id: $id}) SET e.embedding = $embedding",
                {"id": event_id, "embedding": embedding},
            )

        else:
            print("🆕 Creating new memory...")
            event_id = hash_summary(summary)
            self.conn.execute(
                "CREATE (:Event {id: $id, summary: $summary, embedding: $embedding})",
                {"id": event_id, "summary": summary, "embedding": embedding},
            )

        # === 5) Link Event -> Episode (provenance) ===
        self.conn.execute(
            """
            MATCH (e:Event {id: $event_id}), (ep:Episode {id: $episode_id})
            CREATE (e)-[:EXTRACTED_FROM]->(ep)
            """,
            {"event_id": event_id, "episode_id": episode_id},
        )

        # === 6) Update INVOLVES edges (core memory strength) ===
        for entity in entities:
            entity_id = self._ensure_entity(entity)
            rel_resp = self.conn.execute(
                """
                MATCH (e:Event {id: $event_id})-[r:INVOLVES]->(en:Entity {id: $entity_id})
                RETURN r.t_created, r.t_last_active, r.c_valid, r.weight
                """,
                {"event_id": event_id, "entity_id": entity_id},
            )
            if rel_resp.has_next():
                row = rel_resp.get_next()
                c_valid_new = row[2] + 1
                weight_new = self.calculate_weight(current_time, c_valid_new, current_time)
                self.conn.execute(
                    """
                    MATCH (e:Event {id: $event_id})-[r:INVOLVES]->(en:Entity {id: $entity_id})
                    SET r.t_last_active = $t_last,
                        r.c_valid = $c_valid,
                        r.weight = $weight
                    """,
                    {
                        "event_id": event_id,
                        "entity_id": entity_id,
                        "t_last": current_time,
                        "c_valid": c_valid_new,
                        "weight": weight_new,
                    },
                )
                print(
                    f"🔗 Updated INVOLVES {entity_id}: c_valid={c_valid_new}, "
                    f"weight={weight_new:.4f}"
                )
            else:
                weight_init = self.calculate_weight(current_time, 1, current_time)
                self.conn.execute(
                    """
                    MATCH (e:Event {id: $event_id}), (en:Entity {id: $entity_id})
                    CREATE (e)-[:INVOLVES {
                        t_created: $t_created,
                        t_last_active: $t_last,
                        c_valid: $c_valid,
                        weight: $weight
                    }]->(en)
                    """,
                    {
                        "event_id": event_id,
                        "entity_id": entity_id,
                        "t_created": current_time,
                        "t_last": current_time,
                        "c_valid": 1,
                        "weight": weight_init,
                    },
                )
                print(
                    f"🔗 New INVOLVES {entity_id}: c_valid=1, weight={weight_init:.4f}"
                )

        return event_id

    def peek_decayed_weights(self, event_id, current_time):
        # Observation-only query: compute decayed weight without updating DB.
        resp = self.conn.execute(
            """
            MATCH (e:Event {id: $event_id})-[r:INVOLVES]->(en:Entity)
            RETURN e.summary, en.id, r.t_last_active, r.c_valid, r.weight
            """,
            {"event_id": event_id},
        )
        while resp.has_next():
            row = resp.get_next()
            summary, entity_id, t_last, c_valid, stored_weight = row
            decayed = self.calculate_weight(t_last, c_valid, current_time)
            print(
                f"📉 Decayed weight @t={current_time} | {summary} -> {entity_id} | "
                f"stored={stored_weight:.4f}, decayed={decayed:.4f}"
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
