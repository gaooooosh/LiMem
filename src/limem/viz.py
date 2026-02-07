# -*- coding: utf-8 -*-
import os

from .config import DB_PATH, GRAPH_MAX_EVENTS, GRAPH_OUTPUT_DIR, KUZU_EXPLORER_URL


class MemoryVisualizer:
    def __init__(
        self,
        conn,
        output_dir=GRAPH_OUTPUT_DIR,
        db_path=DB_PATH,
        explorer_url=KUZU_EXPLORER_URL,
    ):
        self.conn = conn
        self.output_dir = output_dir
        self.db_path = db_path
        self.explorer_url = explorer_url

    def _write_query(self, query, output_prefix):
        os.makedirs(self.output_dir, exist_ok=True)
        path = os.path.join(self.output_dir, f"{output_prefix}.cypher")
        with open(path, "w", encoding="utf-8") as f:
            f.write(query.strip() + "\n")
        return path

    def _print_explorer_hint(self, title, query_path):
        print(f"INFO: Kuzu Explorer view prepared: {title}")
        if self.explorer_url:
            print(f"INFO: Explorer URL: {self.explorer_url}")
        print(f"INFO: DB path: {self.db_path}")
        print(f"INFO: Cypher file: {query_path}")

    def export_event_subgraph(self, event_id, output_prefix):
        # Kuzu Explorer: query for one Event and its Entities.
        resp = self.conn.execute(
            """
            MATCH (e:Event {id: $event_id})-[r:INVOLVES]->(en:Entity)
            RETURN e, r, en
            """,
            {"event_id": event_id},
        )
        rows = []
        while resp.has_next():
            rows.append(resp.get_next())
        if not rows:
            print("INFO: No INVOLVES edges found for visualization.")
            return None

        query = (
            "MATCH (e:Event {id: '"
            + event_id
            + "'})-[r:INVOLVES]->(en:Entity)\n"
            "RETURN e, r, en;"
        )
        path = self._write_query(query, output_prefix)
        self._print_explorer_hint(f"Event {event_id}", path)
        return path

    def export_memory_graph(self, output_prefix, include_episodes=False, max_events=0):
        # Kuzu Explorer: query for the whole memory graph (Events + Entities [+ Episodes]).
        event_resp = self.conn.execute("MATCH (e:Event) RETURN e.id, e.summary")
        events = []
        while event_resp.has_next():
            events.append(event_resp.get_next())
        if max_events == 0:
            max_events = GRAPH_MAX_EVENTS
        if max_events and len(events) > max_events:
            events = events[:max_events]
        event_ids = [eid for eid, _ in events]

        if not event_ids:
            print("INFO: No memory graph data found.")
            return None

        id_list = ", ".join([f"'{eid}'" for eid in event_ids])
        base_match = (
            "MATCH (e:Event)-[r:INVOLVES]->(en:Entity)\n"
            f"WHERE e.id IN [{id_list}]\n"
        )
        if include_episodes:
            query = (
                base_match
                + "OPTIONAL MATCH (e)-[x:EXTRACTED_FROM]->(ep:Episode)\n"
                + "RETURN e, r, en, x, ep;"
            )
        else:
            query = base_match + "RETURN e, r, en;"

        path = self._write_query(query, output_prefix)
        self._print_explorer_hint("Memory Graph", path)
        return path
