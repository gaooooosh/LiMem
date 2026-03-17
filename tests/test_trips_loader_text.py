# -*- coding: utf-8 -*-
import os
import runpy
import unittest


def _extract_episode_text(record, bucket_name):
    project_root = os.path.dirname(os.path.dirname(__file__))
    loader_path = os.path.join(project_root, "src", "script", "trips_loader.py")
    module_dict = runpy.run_path(loader_path)
    return module_dict["extract_episode_text"](record, bucket_name)


class TestTripsLoaderText(unittest.TestCase):
    def test_extract_episode_text_cleans_screen_app_noise_timestamp(self):
        record = {
            "start_time": "2026-03-12 17:37:01",
            "source": "屏幕",
            "payload": {
                "SCREEN": "副驾屏",
                "APP": "QQ音乐。还有：\nQQ音乐播放；时间:2023-03-08 08:40",
            },
        }

        text = _extract_episode_text(record, "屏幕操作数据")

        self.assertIn("[屏幕操作数据]", text)
        self.assertIn("屏幕: 副驾屏", text)
        self.assertIn("应用: QQ音乐", text)
        self.assertNotIn("2023-03-08 08:40", text)
        self.assertNotIn("还有", text)

    def test_extract_episode_text_uses_detail_first(self):
        record = {
            "detail": "用户说: 播放QQ音乐",
            "payload": {"SCREEN": "副驾屏", "APP": "QQ音乐"},
        }
        text = _extract_episode_text(record, "屏幕操作数据")
        self.assertEqual(text, "用户说: 播放QQ音乐")
