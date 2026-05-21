# -*- coding: utf-8 -*-
import os
import tempfile
import unittest

from limem.core.context import Context
from limem.core.event import Event
from limem.ops import MemoryGraphOps
from limem.retrieval.task_recall import TaskProjector, TaskRecallPipeline
from limem.storage.kuzu_store import KuzuStore


class TestTaskRecall(unittest.TestCase):
    def test_projector_extracts_literal_anchors_without_task_wordlist(self):
        with tempfile.TemporaryDirectory() as td:
            store = KuzuStore(db_path=os.path.join(td, "projection.kz"))
            projector = TaskProjector(store)

            projection = projector.project(
                "请检查 /home/app/main.py 里 ROOT_API_KEY 在 127.0.0.1:8012 "
                "和 v1.2.3 下 38度 场景的行为"
            )

            joined = " ".join(projection.literal_anchors)
            self.assertIn("/home/app/main.py", joined)
            self.assertIn("ROOT_API_KEY", joined)
            self.assertIn("8012", joined)
            self.assertIn("v1.2.3", joined)
            self.assertIn("38度", joined)

    def test_recall_compiles_rule_context_and_event_separately(self):
        with tempfile.TemporaryDirectory() as td:
            store = KuzuStore(db_path=os.path.join(td, "recall.kz"))
            ops = MemoryGraphOps(store)
            ops.register_entity(
                entity_id="LiMem",
                description="LiMem 算法侧记忆系统",
                aliases=["src/limem"],
            )
            ops.put_entity_pattern(
                "LiMem",
                "## 算法边界\n- LiMem 算法逻辑应保持在 src/limem，不要耦合服务日志。",
            )
            event = Event(
                id="evt_root_key",
                summary="上次 LiMem 部署因 ROOT_API_KEY 缺失失败",
                action="部署失败",
                causality="部署前需要检查 .env 中的 ROOT_API_KEY",
                timestamp=100,
                last_active=100,
                created_at=100,
                updated_at=100,
                valid_from=100,
            )
            store.save_event(event)
            store.create_involves_relation(
                event_id=event.id,
                entity_id="LiMem",
                t_created=100,
                t_valid=100,
            )
            context = Context(
                id="ctx_hot_car",
                subtype="environment",
                subject="用户",
                condition="用户处于高温车内出行环境",
                facts={"气温": "38度", "位置": "车内"},
                applies_when="车内播放和舒适度相关任务",
                summary="高温车内环境",
                confidence=0.9,
                support_count=2,
                created_at=100,
                updated_at=100,
                valid_from=100,
                last_seen_at=100,
            )
            store.save_context(context)

            result = ops.recall_for_task(
                "在 LiMem 的 src/limem 里处理车内 38度 场景，并检查 ROOT_API_KEY",
                include_debug=True,
            )

            text = result["prompt_text"]
            self.assertIn("## Relevant Memory", text)
            self.assertIn("[Rule]", text)
            self.assertIn("src/limem", text)
            self.assertIn("[Context]", text)
            self.assertIn("38度", text)
            self.assertIn("[Event]", text)
            self.assertIn("ROOT_API_KEY", text)
            for line in text.splitlines()[1:]:
                self.assertLessEqual(len(line), 140)
                self.assertNotIn("score", line.lower())
                self.assertNotIn("evt_root_key", line)

    def test_update_relation_folds_to_latest_event(self):
        with tempfile.TemporaryDirectory() as td:
            store = KuzuStore(db_path=os.path.join(td, "fold_update.kz"))
            old = Event(
                id="evt_old",
                summary="用户之前选择方案A",
                action="选择方案A",
                timestamp=10,
                last_active=10,
                created_at=10,
                updated_at=10,
                valid_from=10,
            )
            new = Event(
                id="evt_new",
                summary="用户最终改为选择方案B",
                action="选择方案B",
                causality="方案A被后续更新覆盖",
                timestamp=20,
                last_active=20,
                created_at=20,
                updated_at=20,
                valid_from=20,
            )
            store.save_event(old)
            store.save_event(new)
            store.upsert_event_relation(
                from_event_id=old.id,
                to_event_id=new.id,
                relation_type="意义更新",
                description="后续选择覆盖旧方案",
                confidence=0.9,
                evidence_span="最终改为选择方案B",
                source_episode_id="",
                source_session_id="",
                timestamp=20,
            )

            result = MemoryGraphOps(store).recall_for_task("继续处理方案A相关实现")
            text = result["prompt_text"]

            self.assertIn("方案B", text)
            self.assertNotIn("用户之前选择方案A；选择方案A", text)

    def test_shared_context_relation_compiles_grouped_event_result(self):
        with tempfile.TemporaryDirectory() as td:
            store = KuzuStore(db_path=os.path.join(td, "fold_cause.kz"))
            cause = Event(
                id="evt_cause",
                summary="Docker 部署缺少 ROOT_API_KEY",
                action="配置缺失",
                timestamp=10,
                last_active=10,
                created_at=10,
                updated_at=10,
                valid_from=10,
            )
            result = Event(
                id="evt_result",
                summary="服务启动失败",
                action="启动失败",
                causality="部署前需要补齐 .env",
                timestamp=11,
                last_active=11,
                created_at=11,
                updated_at=11,
                valid_from=11,
            )
            store.save_event(cause)
            store.save_event(result)
            store.upsert_event_relation(
                from_event_id=cause.id,
                to_event_id=result.id,
                relation_type="共同背景",
                description="缺少 ROOT_API_KEY 导致服务启动失败",
                confidence=0.9,
                evidence_span="启动失败",
                source_episode_id="",
                source_session_id="",
                timestamp=11,
            )

            output = MemoryGraphOps(store).recall_for_task("部署 Docker 服务时检查 ROOT_API_KEY")
            text = output["prompt_text"]

            self.assertIn("ROOT_API_KEY", text)
            self.assertIn("[Event]", text)
            self.assertIn("启动失败", text)

    def test_legacy_cause_relation_maps_to_shared_context(self):
        with tempfile.TemporaryDirectory() as td:
            store = KuzuStore(db_path=os.path.join(td, "fold_legacy_cause.kz"))
            cause = Event(
                id="evt_cause",
                summary="Docker 部署缺少 ROOT_API_KEY",
                action="配置缺失",
                timestamp=10,
                last_active=10,
                created_at=10,
                updated_at=10,
                valid_from=10,
            )
            result = Event(
                id="evt_result",
                summary="服务启动失败",
                action="启动失败",
                timestamp=11,
                last_active=11,
                created_at=11,
                updated_at=11,
                valid_from=11,
            )
            store.save_event(cause)
            store.save_event(result)
            store.upsert_event_relation(
                from_event_id=cause.id,
                to_event_id=result.id,
                relation_type="导致",
                description="缺少 ROOT_API_KEY 导致服务启动失败",
                confidence=0.9,
                evidence_span="启动失败",
                source_episode_id="",
                source_session_id="",
                timestamp=11,
            )

            output = MemoryGraphOps(store).recall_for_task("部署 Docker 服务时检查 ROOT_API_KEY")
            text = output["prompt_text"]

            self.assertIn("ROOT_API_KEY", text)
            self.assertIn("启动失败", text)

    def test_empty_when_only_generic_similarity(self):
        with tempfile.TemporaryDirectory() as td:
            store = KuzuStore(db_path=os.path.join(td, "empty.kz"))
            store.save_event(
                Event(
                    id="evt_generic",
                    summary="今天讨论了一些事情",
                    action="讨论",
                    timestamp=1,
                    last_active=1,
                    created_at=1,
                    updated_at=1,
                    valid_from=1,
                )
            )

            result = TaskRecallPipeline(store).recall_for_task("请继续处理这个问题")

            self.assertEqual(result["prompt_text"], "")


if __name__ == "__main__":
    unittest.main()
