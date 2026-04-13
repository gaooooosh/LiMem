# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest

from script.session_loader import load_session_episodes


class TestSessionLoader(unittest.TestCase):
    def test_load_session_episodes_builds_one_narrative_per_session(self):
        sample = [
            {
                "user_id": "USR_001",
                "session": {
                    "session_id": "SESS_001",
                    "start_time": "2026-04-01 07:10:00",
                    "end_time": "2026-04-01 07:55:00",
                    "total_duration_min": 45,
                    "triggered_scenes": ["迎宾模式", "沉浸会议"],
                    "session_desc": "工作日早高峰单人通勤场景",
                },
                "log_list": [
                    {
                        "start_time": "2026-04-01 07:10:00",
                        "source": "环境感知",
                        "payload": {
                            "cabin": {"noise_db": 35},
                            "env": {"temp_out": 32.0},
                            "location": {"geo_type": "home"},
                        },
                    },
                    {
                        "start_time": "2026-04-01 07:10:02",
                        "source": "舱内摄像头",
                        "visual": {
                            "DRIVER": {
                                "emotion": "其他",
                                "action": "未说话",
                                "description": "主驾刚上车，佩戴墨镜",
                            },
                            "PASSENGER": {"description": "无其他乘客"},
                        },
                    },
                    {
                        "start_time": "2026-04-01 07:10:06",
                        "source": "车辆状态",
                        "payload": {
                            "body": {
                                "window_pct": [0, 0, 0, 0],
                                "sunroof_pct": 0,
                            },
                            "seat_hvac": {
                                "seat_vent": 2,
                                "hvac_fan": 8,
                                "hvac_temp_set": 16.0,
                                "hvac_mode": "",
                            },
                        },
                    },
                    {
                        "start_time": "2026-04-01 07:10:08",
                        "source": "车机对话",
                        "detail": "2026-04-01 早上7点10分左右，坐在主驾的用户说：理想同学，去公司。 -> 车机回答：好的，已为你规划前往公司的路线",
                    },
                    {
                        "start_time": "2026-04-01 07:30:00",
                        "source": "车辆状态",
                        "payload": {
                            "body": {
                                "window_pct": [0, 0, 0, 0],
                                "sunroof_pct": 0,
                            },
                            "seat_hvac": {
                                "seat_vent": 2,
                                "hvac_fan": 1,
                                "hvac_temp_set": 24.0,
                                "hvac_mode": "face_foot",
                            },
                        },
                    },
                ],
            }
        ]

        with tempfile.TemporaryDirectory() as td:
            sessions_path = os.path.join(td, "session_v1.json")
            with open(sessions_path, "w", encoding="utf-8") as f:
                json.dump(sample, f, ensure_ascii=False)

            episodes = load_session_episodes(sessions_path)

        self.assertEqual(len(episodes), 1)
        episode = episodes[0]
        self.assertIn("【会话概要】工作日早高峰单人通勤场景", episode.content)
        self.assertIn("【触发场景】迎宾模式, 沉浸会议", episode.content)
        self.assertIn("【时间范围】2026-04-01 07:10 ~ 2026-04-01 07:55 (45分钟)", episode.content)
        self.assertIn("[07:10] 环境感知: 室外32°C, 噪音35dB, 位置=home", episode.content)
        self.assertIn("[07:10] 舱内摄像头: 主驾: 主驾刚上车，佩戴墨镜", episode.content)
        self.assertIn("[07:10] 车辆状态: 座椅通风=2, 空调风量=8, 空调温度=16°C", episode.content)
        self.assertIn("理想同学，去公司", episode.content)
        self.assertIn("[07:30] 车辆状态变化: 空调风量 8→1, 空调温度 16°C→24°C", episode.content)
        self.assertEqual(episode.metadata["session_id"], "SESS_001")
        self.assertEqual(episode.metadata["user_id"], "USR_001")
        self.assertEqual(episode.metadata["log_count"], 5)
        self.assertEqual(
            episode.metadata["sources"],
            ["环境感知", "舱内摄像头", "车辆状态", "车机对话"],
        )

    def test_include_sources_filters_timeline_but_keeps_session_episode(self):
        sample = [
            {
                "user_id": "USR_002",
                "session": {
                    "session_id": "SESS_002",
                    "start_time": "2026-04-01 18:20:00",
                    "end_time": "2026-04-01 18:30:00",
                    "session_desc": "接孩子回家",
                },
                "log_list": [
                    {
                        "start_time": "2026-04-01 18:20:00",
                        "source": "环境感知",
                        "payload": {"cabin": {"noise_db": 37}},
                    },
                    {
                        "start_time": "2026-04-01 18:20:08",
                        "source": "车机对话",
                        "payload": {
                            "query": "理想同学，导航回家。",
                            "tts": "好的，已为你规划回家的路线。",
                        },
                    },
                ],
            }
        ]

        with tempfile.TemporaryDirectory() as td:
            sessions_path = os.path.join(td, "session_v1.json")
            with open(sessions_path, "w", encoding="utf-8") as f:
                json.dump(sample, f, ensure_ascii=False)

            episodes = load_session_episodes(
                sessions_path,
                include_sources={"车机对话"},
            )

        self.assertEqual(len(episodes), 1)
        episode = episodes[0]
        self.assertNotIn("环境感知", episode.content)
        self.assertIn("[18:20] 车机对话: 用户说「理想同学，导航回家」→「好的，已为你规划回家的路线」", episode.content)
        self.assertEqual(episode.metadata["log_count"], 1)
        self.assertEqual(episode.metadata["sources"], ["车机对话"])


if __name__ == "__main__":
    unittest.main()
