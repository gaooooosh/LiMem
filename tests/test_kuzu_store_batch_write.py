# -*- coding: utf-8 -*-
import os
import tempfile
import unittest

from limem.core.episode import Episode
from limem.core.event import Event
from limem.core.context import Context
from limem.storage.kuzu_store import KuzuStore


class TestKuzuStoreBatchWrite(unittest.TestCase):
    def test_save_events_batch_and_link_batch_preserve_graph_integrity(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "batch_write.kz")
            store = KuzuStore(db_path=db_path)
            episode = Episode(id="ep_batch", content="trip payload", timestamp=101)
            store.save_episode(episode)

            event_a = Event(
                id="evt_batch_a",
                summary="用户发起导航",
                action="发起导航",
                time_range={"start": 101, "end": 101, "display_time_bucket": "morning"},
                timestamp=101,
                last_active=101,
                created_at=101,
                updated_at=101,
                valid_from=101,
                valid_to=None,
                embedding=[0.0] * store.embedding_dim,
            )
            event_b = Event(
                id="evt_batch_b",
                summary="系统开始规划路线",
                action="规划路线",
                causality="响应导航请求",
                time_range={"start": 102, "end": 102, "display_time_bucket": "morning"},
                timestamp=102,
                last_active=102,
                created_at=102,
                updated_at=102,
                valid_from=102,
                valid_to=120,
                embedding=[0.1] * store.embedding_dim,
            )

            store.save_events_batch([event_a, event_b])
            store.link_events_to_episode_batch([event_a.id, event_b.id], episode.id)

            self.assertIsNotNone(store.get_event(event_a.id))
            self.assertIsNotNone(store.get_event(event_b.id))
            self.assertIsNone(store.get_event(event_a.id).valid_to)
            self.assertEqual(store.get_event(event_b.id).valid_to, 120)

            resp = store.conn.execute(
                """
                MATCH (:Event)-[r:EXTRACTED_FROM]->(:Episode {id: $episode_id})
                RETURN count(r)
                """,
                {"episode_id": episode.id},
            )
            self.assertTrue(resp.has_next())
            self.assertEqual(resp.get_next()[0], 2)

    def test_context_card_fields_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "context_card.kz")
            store = KuzuStore(db_path=db_path)
            context = Context(
                id="ctx_card",
                subtype="environment",
                subject="用户",
                condition="用户处于高温车内出行环境",
                facts={"气温": "38度", "位置": "车内"},
                applies_when="用户进行车内舒适度相关交互",
                summary="用户处于高温车内出行环境",
                confidence=0.88,
                created_at=101,
                updated_at=101,
                valid_from=101,
                last_seen_at=101,
                embedding=[0.0] * store.embedding_dim,
            )

            store.save_context(context)
            saved = store.get_context("ctx_card")

            self.assertIsNotNone(saved)
            self.assertEqual(saved.subject, "用户")
            self.assertEqual(saved.condition, "用户处于高温车内出行环境")
            self.assertEqual(saved.facts, {"气温": "38度", "位置": "车内"})
            self.assertEqual(saved.applies_when, "用户进行车内舒适度相关交互")


if __name__ == "__main__":
    unittest.main()
