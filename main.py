# -*- coding: utf-8 -*-
import hashlib
import json
import math
import os
import uuid
from datetime import datetime

import kuzu
import dashscope
from dashscope import Generation, TextEmbedding
from dotenv import load_dotenv

load_dotenv()

# =========================
# Experiment Knobs (Research First)
# =========================
# These parameters correspond to the hyperparameters in the paper/algorithm.
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.85"))
DECAY_RATE = float(os.getenv("DECAY_RATE", "0.01"))
EPISODE_TTL = int(os.getenv("EPISODE_TTL", "3600"))
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = os.getenv(
    "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/api/v1"
)
GENERATION_MODEL = os.getenv("GENERATION_MODEL", "qwen3-1.7b")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v2")
TEST_DATA_PATH = os.getenv("TEST_DATA_PATH", "./example.json")
TEST_DATASET_KEY = os.getenv("TEST_DATASET_KEY", "input_records_1_mock")
MAX_EPISODES = int(os.getenv("MAX_EPISODES", "6"))
EXPORT_GRAPH = os.getenv("EXPORT_GRAPH", "true").lower() == "true"
EXPORT_EVERY_EPISODE = os.getenv("EXPORT_EVERY_EPISODE", "true").lower() == "true"
GRAPH_OUTPUT_DIR = os.getenv("GRAPH_OUTPUT_DIR", "./outputs")
GRAPH_EXPORT_FORMAT = os.getenv("GRAPH_EXPORT_FORMAT", "png")


def init_db(conn):
    # Schema mirrors the paper's memory graph: Episodes (raw), Events (summaries),
    # Entities (symbols), and relations for memory consolidation and provenance.
    conn.execute(
        """
        CREATE NODE TABLE IF NOT EXISTS Episode(
            id STRING,
            content STRING,
            timestamp INT64,
            PRIMARY KEY(id)
        )
        """
    )
    conn.execute(
        """
        CREATE NODE TABLE IF NOT EXISTS Event(
            id STRING,
            summary STRING,
            embedding FLOAT[1536],
            PRIMARY KEY(id)
        )
        """
    )
    conn.execute(
        """
        CREATE NODE TABLE IF NOT EXISTS Entity(
            id STRING,
            type STRING,
            PRIMARY KEY(id)
        )
        """
    )
    conn.execute(
        """
        CREATE REL TABLE IF NOT EXISTS INVOLVES(
            FROM Event TO Entity,
            t_created INT64,
            t_last_active INT64,
            c_valid INT64,
            weight DOUBLE
        )
        """
    )
    conn.execute(
        """
        CREATE REL TABLE IF NOT EXISTS EXTRACTED_FROM(
            FROM Event TO Episode
        )
        """
    )


def hash_summary(summary):
    # Event ID is a deterministic hash of its summary (as specified).
    return hashlib.sha256(summary.encode("utf-8")).hexdigest()


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
        resp = Generation.call(
            api_key=dashscope.api_key,
            model=GENERATION_MODEL,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            result_format="message",
            enable_thinking=True,
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

    def _dot_escape(self, text):
        return text.replace("\\", "\\\\").replace('"', '\\"')

    def export_event_subgraph(self, event_id, output_prefix):
        # Visualization: show the extracted subgraph for one Event and its Entities.
        resp = self.conn.execute(
            """
            MATCH (e:Event {id: $event_id})-[r:INVOLVES]->(en:Entity)
            RETURN e.summary, en.id, en.type, r.weight, r.c_valid, r.t_last_active
            """,
            {"event_id": event_id},
        )
        rows = []
        while resp.has_next():
            rows.append(resp.get_next())
        if not rows:
            print("⚠️ No INVOLVES edges found for visualization.")
            return None

        summary = rows[0][0]
        dot_lines = [
            "digraph LTM {",
            "  rankdir=LR;",
            "  node [shape=box, style=rounded];",
            f'  event [label="{self._dot_escape("Event")}\\n{self._dot_escape(summary)}"];',
        ]
        for row in rows:
            _, entity_id, entity_type, weight, c_valid, t_last = row
            node_name = f"ent_{hashlib.md5(entity_id.encode('utf-8')).hexdigest()[:8]}"
            label = f"Entity\\n{entity_id}\\n({entity_type})"
            dot_lines.append(
                f'  {node_name} [shape=ellipse, label="{self._dot_escape(label)}"];'
            )
            edge_label = f"w={weight:.4f}\\nc={c_valid}\\nlast={t_last}"
            dot_lines.append(
                f'  event -> {node_name} [label="{self._dot_escape(edge_label)}"];'
            )
        dot_lines.append("}")
        dot = "\n".join(dot_lines)

        os.makedirs(GRAPH_OUTPUT_DIR, exist_ok=True)
        output_prefix = os.path.join(GRAPH_OUTPUT_DIR, output_prefix)

        try:
            from graphviz import Source

            src = Source(dot)
            out_path = src.render(
                filename=output_prefix, format=GRAPH_EXPORT_FORMAT, cleanup=True
            )
            print(f"🖼️ Graph rendered: {out_path}")
            return out_path
        except Exception:
            dot_path = f"{output_prefix}.dot"
            with open(dot_path, "w", encoding="utf-8") as f:
                f.write(dot)
            print(f"📄 Graphviz not available; wrote DOT: {dot_path}")
            return dot_path

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
        # Recall step: vector search over existing Event embeddings.
        resp = self.conn.execute(
            """
            MATCH (e:Event)
            RETURN e.id AS id, e.summary AS summary,
                   cosine_similarity(e.embedding, $embedding) AS sim
            ORDER BY sim DESC
            LIMIT 1
            """,
            {"embedding": embedding},
        )
        if not resp.has_next():
            return None, None, None
        row = resp.get_next()
        return row[0], row[1], row[2]

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


def parse_time_to_unix(ts):
    # Simple parser for example.json timestamps (YYYY-MM-DD HH:MM:SS).
    return int(datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").timestamp())


def load_test_episodes(path, dataset_key, max_items):
    # Experimental data loader: keep it flat and transparent for debugging.
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    records = data.get(dataset_key, [])
    episodes = []
    for rec in records:
        if rec.get("source") != "车机对话":
            continue
        payload = rec.get("payload", {})
        detail = rec.get("detail", "")
        if detail:
            text = detail
        else:
            query = payload.get("query", "")
            tts = payload.get("tts", "")
            text = f"用户说: {query} -> 车机回答: {tts}"
        ts = rec.get("start_time", "")
        if ts:
            unix_ts = parse_time_to_unix(ts)
        else:
            unix_ts = 0
        episodes.append((text, unix_ts))
        if max_items and len(episodes) >= max_items:
            break
    return episodes


if __name__ == "__main__":
    # 0) Init DB
    db = kuzu.Database("./demo_db")
    conn = kuzu.Connection(db)
    init_db(conn)
    ltm = ResearchLTM(conn)

    # 1) Load test episodes from example.json and process sequentially.
    episodes = load_test_episodes(TEST_DATA_PATH, TEST_DATASET_KEY, MAX_EPISODES)
    print(
        f"📦 Loaded {len(episodes)} episodes from {TEST_DATA_PATH} | {TEST_DATASET_KEY}"
    )
    last_event_id = None
    last_time = 0
    for text, ts in episodes:
        last_event_id = ltm.process_new_episode(text, ts)
        last_time = ts
        if EXPORT_GRAPH and EXPORT_EVERY_EPISODE and last_event_id:
            ltm.export_event_subgraph(last_event_id, f"event_{last_event_id}")

    # 2) Peek decay after a long gap (no DB update).
    if last_event_id:
        future_time = last_time + 100000
        ltm.peek_decayed_weights(last_event_id, future_time)
        if EXPORT_GRAPH and not EXPORT_EVERY_EPISODE:
            ltm.export_event_subgraph(last_event_id, f"event_{last_event_id}")

    # 3) TTL cleanup (Episodes only).
    ltm.cleanup_ttl(last_time + 100001)
