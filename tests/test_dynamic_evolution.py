# -*- coding: utf-8 -*-
import os
import tempfile
import unittest

from limem import create_ltm, Episode, migrate_to_dynamic_graph


class TestDynamicEvolution(unittest.TestCase):
    def test_append_first_and_dynamic_edges(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "test_dynamic.kz")
            ltm = create_ltm(
                db_path=db_path,
                config={
                    "offline_mode": True,
                    "enable_dynamic_evolution": True,
                    "append_first_mode": True,
                    "generate_answer": False,
                },
            )

            ltm.ingest(Episode(content="用户说: 我要开会，开勿扰", timestamp=1773326409))
            ltm.ingest(Episode(content="用户说: 导航去公司", timestamp=1773326500))

            stats = ltm.get_stats()
            self.assertGreaterEqual(stats.get("event_count", 0), 2)
            self.assertGreaterEqual(stats.get("context_count", 0), 1)
            self.assertGreaterEqual(stats.get("abstract_to_count", 0), 1)

            report = migrate_to_dynamic_graph(ltm.store, dry_run=True).to_dict()
            self.assertIn("scanned_involves", report)
            self.assertTrue(report["dry_run"])


if __name__ == "__main__":
    unittest.main()
