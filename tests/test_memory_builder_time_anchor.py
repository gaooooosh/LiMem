# -*- coding: utf-8 -*-
import unittest

from limem.builder.memory_builder import MemoryBuilder
from limem.core.episode import Episode


class _NoopExtractor:
    def extract(self, text):  # pragma: no cover - not used in this unit test
        raise NotImplementedError


class _NoopConsolidator:
    pass


class _NoopStore:
    pass


class TestMemoryBuilderTimeAnchor(unittest.TestCase):
    def test_reanchor_far_away_time_for_telemetry_episode(self):
        builder = MemoryBuilder(
            extractor=_NoopExtractor(),
            consolidator=_NoopConsolidator(),
            store=_NoopStore(),
        )
        episode = Episode(
            content="[屏幕操作数据] 屏幕: 副驾屏 | 应用: QQ音乐",
            timestamp=1773317821,
            metadata={"start_time": "2026-03-12 17:37:01"},
        )

        should = builder._should_reanchor_event_time(
            time_range={"start": 1678264800, "end": 1678264800},
            episode=episode,
            current_time=1773317821,
        )
        self.assertTrue(should)

    def test_keep_far_away_time_when_episode_is_retrospective(self):
        builder = MemoryBuilder(
            extractor=_NoopExtractor(),
            consolidator=_NoopConsolidator(),
            store=_NoopStore(),
        )
        episode = Episode(
            content="用户回顾去年听歌偏好",
            timestamp=1773317821,
            metadata={"start_time": "2026-03-12 17:37:01"},
        )

        should = builder._should_reanchor_event_time(
            time_range={"start": 1678264800, "end": 1678264800},
            episode=episode,
            current_time=1773317821,
        )
        self.assertFalse(should)


if __name__ == "__main__":
    unittest.main()
