# -*- coding: utf-8 -*-
"""MemoryBuilder - 记忆构建器

编排整个构建管道：
1. LLM提取（事件 + 实体）
2. 相似度搜索
3. 合并/创建决策
4. 存储（Event + INVOLVES关系）
5. 修剪/提升
"""

from dataclasses import dataclass
from typing import Any, Optional
import uuid
import time
import re

try:
    from dashscope import TextEmbedding
except Exception:  # pragma: no cover - optional dependency for offline mode
    TextEmbedding = None

from ..core.episode import Episode
from ..core.event import Event, EventRelation
from ..core.memory import IngestResult
from ..config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    EMBEDDING_MODEL,
    DEFAULT_USER_ID,
    APPEND_FIRST_MODE,
    ENABLE_LEGACY_ONLINE_EVENT_MERGE,
    PRUNE_C_VALID_THRESHOLD,
    PRUNE_EVIDENCE_TOP_K,
)
from ..utils import hash_summary, time_bucket_from_ts
from .extractor import LLMExtractor, ExtractionResult
from .consolidator import Consolidator, ConsolidationResult


@dataclass
class BuilderConfig:
    """构建器配置

    Attributes:
        prune_threshold: 修剪阈值（c_valid）
        prune_top_k: 证据修剪数量
        default_user_id: 默认用户ID
        append_first_mode: 是否启用append-first事件写入
    """

    prune_threshold: int = PRUNE_C_VALID_THRESHOLD
    prune_top_k: int = PRUNE_EVIDENCE_TOP_K
    default_user_id: str = DEFAULT_USER_ID
    append_first_mode: bool = APPEND_FIRST_MODE
    enable_legacy_online_event_merge: bool = ENABLE_LEGACY_ONLINE_EVENT_MERGE


class MemoryBuilder:
    """记忆构建器 - 编排完整的构建管道

    职责：协调提取、合并、存储的完整流程。

    管道流程：
    1. 保存原始Episode
    2. LLM提取（事件 + 实体）
    3. 生成嵌入向量
    4. 相似度搜索
    5. 合并/创建决策
    6. 存储Event
    7. 创建Event → Episode链接
    8. 更新INVOLVES关系
    9. 修剪/提升（可选）
    """

    def __init__(
        self,
        extractor: LLMExtractor,
        consolidator: Consolidator,
        store,  # GraphStore
        config: Optional[BuilderConfig] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        embedding_model: Optional[str] = None,
        dynamic_engine=None,
    ):
        """初始化记忆构建器

        Args:
            extractor: LLM提取器
            consolidator: 记忆合并器
            store: 图存储接口
            config: 构建器配置
            api_key: DashScope API Key
            base_url: DashScope API URL
            embedding_model: 嵌入模型名称
        """
        self.extractor = extractor
        self.consolidator = consolidator
        self.store = store
        self.config = config or BuilderConfig()

        # 配置嵌入服务
        self.api_key = api_key or DASHSCOPE_API_KEY
        self.base_url = base_url or DASHSCOPE_BASE_URL
        self.embedding_model = embedding_model or EMBEDDING_MODEL
        self.dynamic_engine = dynamic_engine

    def build(self, episode: Episode) -> IngestResult:
        """从Episode构建记忆

        这是最核心的方法，编排整个构建流程。

        Args:
            episode: 原始对话片段

        Returns:
            IngestResult 包含事件和构建信息
        """
        current_time = episode.timestamp

        # Step 1: 保存原始Episode
        self.store.save_episode(episode)
        print(f"📝 Saved Episode: {episode.id} at t={current_time}")

        # Step 2: LLM提取
        extraction = self.extractor.extract(episode.content)

        event_payloads = self._collect_event_payloads(extraction)
        if not event_payloads:
            event_payloads = [{}]
        print(f"🧠 Extracted Events: {len(event_payloads)}")

        # 获取实体
        entities = extraction.entities
        print(f"🧩 Entities: {entities}")
        built_events: list[Event] = []
        is_new_flags: list[bool] = []
        merged_targets: list[Optional[str]] = []
        entities_created_total = 0

        for idx, event_payload in enumerate(event_payloads):
            event_payload = self._refine_event_payload(event_payload, episode, current_time)
            event = self._build_event_frame(event_payload, episode, current_time, index=idx)
            if not self._is_effective_event(event):
                continue
            print(f"   - Event[{idx}]: {event.summary}")

            # Step 3: 生成嵌入向量
            embedding = self._get_embedding(event.summary)

            # Event is the append-first atomic memory unit.
            # Online overwrite/merge remains compatibility-only and is disabled by default.
            if self.config.append_first_mode or not self.config.enable_legacy_online_event_merge:
                event.id = self._append_first_event_id(event)
                event.embedding = embedding
                event.timestamp = event.timestamp or current_time
                event.created_at = event.created_at or current_time
                event.updated_at = current_time
                event.valid_from = event.valid_from or current_time
                event.status = event.status or "active"
                self.store.save_event(event)
                is_new = True
                consolidation = ConsolidationResult(should_merge=False)
                print(f"🆕 Append-first event created: {event.id[:12]}...")
            else:
                print("⚠️ Legacy online event merge compatibility mode enabled")
                consolidation = self.consolidator.find_similar_event(
                    embedding=embedding,
                    entities=entities,
                    action=event.action,
                    current_time=current_time,
                )

                if consolidation.similarity_score > 0:
                    print(f"🔎 Top match (combined={consolidation.similarity_score:.4f})")
                    if consolidation.debug_info:
                        debug = consolidation.debug_info
                        print(
                            f"   └─ semantic={debug.get('semantic', 0):.3f}, "
                            f"entity={debug.get('entity', 0):.3f}, "
                            f"time={debug.get('time', 0):.3f}, "
                            f"action={debug.get('action', 0):.3f}"
                        )

                if consolidation.should_merge:
                    event = self._merge_event(
                        event_id=consolidation.target_event_id,
                        incoming_event=event,
                        embedding=embedding,
                        current_time=current_time,
                    )
                    is_new = False
                    print(f"🔍 Merged into existing memory: {consolidation.target_event_id[:8]}...")
                else:
                    event.id = hash_summary(event.summary)
                    event.embedding = embedding
                    self.store.save_event(event)
                    is_new = True
                    print(f"🆕 Created new memory: {event.id[:8]}...")

            # Step 6: 创建Event → Episode链接
            self.store.link_event_to_episode(event.id, episode.id)

            # Step 7: 更新INVOLVES关系
            entities_created_total += self._update_entity_relations(
                event_id=event.id,
                entities=entities,
                current_time=current_time,
            )
            built_events.append(event)
            is_new_flags.append(is_new)
            merged_targets.append(consolidation.target_event_id if not is_new else None)

        if not built_events:
            if not self._should_persist_fallback_event(episode.content):
                skipped_event = Event(
                    id="",
                    summary=self._skipped_episode_summary(episode),
                    action="",
                    causality="",
                    time_range={"start": current_time, "end": current_time},
                    timestamp=current_time,
                    last_active=current_time,
                    created_at=current_time,
                    updated_at=current_time,
                    valid_from=current_time,
                    status="skipped",
                    payload={
                        "episode_id": episode.id,
                        "episode_text": episode.content,
                        "episode_metadata": dict(episode.metadata or {}),
                        "skip_reason": "low_signal_episode",
                    },
                )
                return IngestResult(
                    event=skipped_event,
                    is_new=False,
                    merged_with=None,
                    entities_created=0,
                    events=[],
                )

            fallback_event = self._build_event_frame({}, episode, current_time, index=0)
            fallback_event.id = self._append_first_event_id(fallback_event)
            fallback_event.embedding = self._get_embedding(fallback_event.summary)
            self.store.save_event(fallback_event)
            self.store.link_event_to_episode(fallback_event.id, episode.id)
            built_events = [fallback_event]
            is_new_flags = [True]
            merged_targets = [None]

        # Dynamic evolution updates are strictly local and incremental.
        if self.dynamic_engine:
            self.dynamic_engine.evolve_existing_events(built_events)

        return IngestResult(
            event=built_events[0],
            is_new=is_new_flags[0],
            merged_with=merged_targets[0],
            entities_created=entities_created_total,
            events=built_events,
        )

    def _build_event_frame(
        self,
        data: dict[str, Any],
        episode: Episode,
        current_time: int,
        index: int = 0,
    ) -> Event:
        """构建事件帧

        Args:
            extraction: 提取结果
            episode: 原始Episode
            current_time: 当前时间戳

        Returns:
            Event实例
        """
        # 使用摘要或截取Episode内容作为摘要
        summary = data.get("summary", "")
        if not summary and self._should_persist_fallback_event(episode.content):
            summary = self._fallback_event_summary(episode.content)

        # 构建时间范围
        time_range = data.get("time_range", {})
        if time_range.get("start", 0) == 0:
            time_range["start"] = current_time
        if time_range.get("end", 0) == 0:
            time_range["end"] = current_time
        if not time_range.get("display_time_bucket", ""):
            time_range["display_time_bucket"] = time_bucket_from_ts(current_time)

        payload = dict(data)
        payload["episode_id"] = episode.id
        payload["episode_text"] = episode.content
        payload["event_index"] = int(index)
        if episode.metadata:
            payload["episode_metadata"] = dict(episode.metadata)

        return Event(
            id=hash_summary(summary) if summary else "",
            summary=summary,
            action=data.get("action", ""),
            causality=data.get("causality", ""),
            time_range=time_range,
            last_active=current_time,
            participants=data.get("participants", []),
            evidence=data.get("evidence", []),
            timestamp=current_time,
            created_at=current_time,
            updated_at=current_time,
            valid_from=current_time,
            payload=payload,
            status=str(data.get("status", "active")),
        )

    def _refine_event_payload(
        self,
        data: dict[str, Any],
        episode: Episode,
        current_time: int,
    ) -> dict[str, Any]:
        payload = dict(data or {})
        payload["time_range"] = self._sanitize_extracted_time_range(
            payload.get("time_range", {}),
            current_time=current_time,
        )
        payload["summary"] = self._refine_extracted_summary(
            payload=payload,
            episode=episode,
        )
        return payload

    def _collect_event_payloads(self, extraction: ExtractionResult) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        if isinstance(extraction.events_data, list):
            payloads.extend(item for item in extraction.events_data if isinstance(item, dict))
        if isinstance(extraction.event_data, dict) and extraction.event_data:
            signature = self._event_payload_signature(extraction.event_data)
            existing = {self._event_payload_signature(item) for item in payloads}
            if signature and signature not in existing:
                payloads.insert(0, extraction.event_data)
        return payloads

    def _event_payload_signature(self, payload: dict[str, Any]) -> str:
        return "|".join(
            [
                str(payload.get("summary", "") or "").strip(),
                str(payload.get("action", "") or "").strip(),
                str(payload.get("causality", "") or "").strip(),
            ]
        )

    def _is_effective_event(self, event: Event) -> bool:
        if (event.summary or "").strip():
            return True
        if (event.action or "").strip():
            return True
        if (event.causality or "").strip():
            return True
        return False

    def _should_persist_fallback_event(self, text: str) -> bool:
        normalized = str(text or "").strip()
        if not normalized:
            return False
        if normalized.startswith("{") or normalized.startswith("[{"):
            return False
        lowered = normalized.lower()
        if any(marker in lowered for marker in ['{"start_time"', '"payload"', '"body_status"', '"seat_and_thermal"']):
            return False
        signal_hints = [
            "用户说", "车机回答", "导航", "播放", "打开", "关闭", "开启", "停止",
            "切换", "设置", "开始", "暂停", "恢复", "提醒", "请求", "检测到",
        ]
        if any(token in normalized for token in signal_hints):
            return True
        return False

    def _sanitize_extracted_time_range(
        self,
        time_range: Any,
        current_time: int,
    ) -> dict[str, Any]:
        data = dict(time_range or {}) if isinstance(time_range, dict) else {}
        min_valid_ts = 946684800  # 2000-01-01
        max_delta = 86400 * 30

        start = int(data.get("start", 0) or 0)
        end = int(data.get("end", 0) or 0)
        if start <= 0 or start < min_valid_ts or abs(start - current_time) > max_delta:
            start = current_time
        if end <= 0 or end < min_valid_ts or abs(end - current_time) > max_delta:
            end = start
        display = str(data.get("display_time_bucket", "") or "").strip()
        if not display:
            display = time_bucket_from_ts(start)
        return {
            "start": start,
            "end": end,
            "display_time_bucket": display,
        }

    def _refine_extracted_summary(
        self,
        payload: dict[str, Any],
        episode: Episode,
    ) -> str:
        summary = str(payload.get("summary", "") or "").strip()
        original_summary = summary
        if summary:
            summary = re.sub(r"[；;]\s*时间[:：][^；;]+$", "", summary).strip("；; ")

        bucket = str((episode.metadata or {}).get("bucket_name", "") or "").strip()
        content = str(episode.content or "")

        if bucket == "媒体播放数据":
            media_summary = self._media_event_summary_from_text(content)
            if media_summary and (
                not summary
                or summary.endswith("播放")
                or "时间:" in original_summary
                or len(summary) <= 10
            ):
                summary = media_summary

        if bucket == "车机对话数据":
            dialog_summary = self._dialog_event_summary_from_text(content)
            if dialog_summary and (not summary or len(summary) > 48):
                summary = dialog_summary

        if not summary and self._should_persist_fallback_event(content):
            summary = self._fallback_event_summary(content)

        return summary

    def _media_event_summary_from_text(self, text: str) -> str:
        normalized = str(text or "")
        title_match = re.search(r"《([^》]+)》", normalized)
        app_match = re.search(r"(QQ音乐|喜马拉雅|网易云音乐|酷狗音乐|酷我音乐)", normalized)
        title = title_match.group(1).strip() if title_match else ""
        app = app_match.group(1).strip() if app_match else ""
        if app and title:
            return f"{app}播放《{title}》"
        if title:
            return f"播放《{title}》"
        return ""

    def _dialog_event_summary_from_text(self, text: str) -> str:
        normalized = str(text or "")
        normalized = re.sub(r"^\[[^\]]+\]\s*", "", normalized)
        match = re.search(r"用户说[:：]\s*(.+?)(?:\s*\|\s*车机回答[:：]|\s*->|$)", normalized)
        if not match:
            return ""
        query = match.group(1).strip()
        query = re.sub(r"^(理想同学|小爱同学|你好|你好啊)[，,\s]*", "", query).strip()
        if not query:
            return ""
        return f"用户请求{query}"[:120]

    def _fallback_event_summary(self, text: str) -> str:
        normalized = str(text or "").strip()
        normalized = re.sub(r"^\[[^\]]+\]\s*", "", normalized)
        dialog_summary = self._dialog_event_summary_from_text(normalized)
        if dialog_summary:
            return dialog_summary
        media_summary = self._media_event_summary_from_text(normalized)
        if media_summary:
            return media_summary
        parts = [part.strip() for part in normalized.split("|") if part.strip()]
        informative = self._pick_informative_summary_part(parts)
        if informative:
            normalized = informative
        elif parts:
            normalized = parts[0]
        normalized = re.sub(r"\s+", " ", normalized).strip(" ，,；;")
        return normalized[:120]

    def _pick_informative_summary_part(self, parts: list[str]) -> str:
        if not parts:
            return ""

        def score(part: str) -> tuple[int, int]:
            text = str(part or "").strip()
            dynamic_hints = [
                "打开", "关闭", "开启", "停止", "暂停", "恢复", "播放", "导航",
                "设置", "切换", "开始", "请求", "进入", "触发", "提醒", "检测",
            ]
            low_signal_nouns = {
                "副驾屏", "中控屏", "主驾屏", "后排屏", "会议模式", "QQ音乐", "喜马拉雅",
            }
            dynamic_score = sum(1 for hint in dynamic_hints if hint in text)
            if text in low_signal_nouns:
                dynamic_score -= 2
            return dynamic_score, len(text)

        best = max(parts, key=score)
        return best if score(best)[0] > 0 else ""

    def _skipped_episode_summary(self, episode: Episode) -> str:
        bucket = str((episode.metadata or {}).get("bucket_name", "") or "").strip()
        if bucket:
            return f"skipped: {bucket} low-signal record"
        return "skipped: low-signal record"

    def _merge_event(
        self,
        event_id: str,
        incoming_event: Event,
        embedding: list[float],
        current_time: int,
    ) -> Event:
        """合并到现有事件

        Args:
            event_id: 目标事件ID
            incoming_event: 新事件
            embedding: 嵌入向量
            current_time: 当前时间戳

        Returns:
            合并后的事件
        """
        # 获取现有事件
        existing = self.store.get_event(event_id)
        if not existing:
            raise ValueError(f"Event not found: {event_id}")

        # 合并证据
        merged_evidence = existing.evidence + incoming_event.evidence

        # 更新事件
        existing.last_active = current_time
        existing.evidence = merged_evidence
        existing.embedding = embedding

        # 保存更新
        self.store.update_event(existing)

        return existing

    def _update_entity_relations(
        self,
        event_id: str,
        entities: list[str],
        current_time: int,
    ) -> int:
        """更新实体关系

        Args:
            event_id: 事件ID
            entities: 实体列表
            current_time: 当前时间戳

        Returns:
            新创建的实体数量
        """
        count = 0

        for entity in entities:
            # 获取实体名称
            entity_name = self._get_entity_name(entity)
            if not entity_name:
                continue

            # 确保实体存在
            is_new = self.store.ensure_entity(
                entity_name,
                entity.get("type", "UNKNOWN") if isinstance(entity, dict) else "UNKNOWN",
            )
            if is_new:
                count += 1

            # 更新或创建INVOLVES关系
            relation = self.store.get_involves_relation(event_id, entity_name)

            if relation:
                # 更新现有关系
                relation.c_valid += 1
                relation.t_valid = current_time
                self.store.update_involves_relation(relation)
                c_valid_new = relation.c_valid
            else:
                # 创建新关系
                self.store.create_involves_relation(
                    event_id=event_id,
                    entity_id=entity_name,
                    t_created=current_time,
                    t_valid=current_time,
                    c_valid=1,
                )
                c_valid_new = 1

            # 检查是否需要修剪/提升
            if c_valid_new > self.config.prune_threshold:
                self._prune_and_promote(event_id, current_time)

        return count

    def _prune_and_promote(self, event_id: str, current_time: int) -> None:
        """修剪证据并提升为永久特征

        Args:
            event_id: 事件ID
            current_time: 当前时间戳
        """
        # 修剪证据
        self._prune_event_evidence(event_id)

        # 提升为永久特征
        self.store.promote_permanent_trait(
            user_id=self.config.default_user_id,
            event_id=event_id,
            t_created=current_time,
        )

    def _prune_event_evidence(self, event_id: str) -> None:
        """修剪事件证据

        保留置信度最高的 Top-K 证据。

        Args:
            event_id: 事件ID
        """
        event = self.store.get_event(event_id)
        if not event or not event.evidence:
            return

        # 按置信度排序
        sorted_evidence = sorted(
            event.evidence,
            key=lambda item: float(item.get("confidence", 0.0)),
            reverse=True,
        )

        # 保留 Top-K
        event.evidence = sorted_evidence[: self.config.prune_top_k]
        self.store.update_event(event)

    def _get_embedding(self, text: str) -> list[float]:
        """获取文本嵌入向量

        Args:
            text: 输入文本

        Returns:
            嵌入向量
        """
        if TextEmbedding is None:
            # Offline deterministic embedding fallback
            import hashlib
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            dim = 1536
            vec = [0.0] * dim
            for i, b in enumerate(digest):
                vec[i % dim] += (b / 255.0) * 2.0 - 1.0
            norm = sum(v * v for v in vec) ** 0.5
            return [v / norm for v in vec] if norm else vec

        import dashscope
        dashscope.base_http_api_url = self.base_url
        dashscope.api_key = self.api_key

        resp = TextEmbedding.call(model=self.embedding_model, input=text)
        output = resp.output

        if isinstance(output, dict):
            return output["embeddings"][0]["embedding"]
        return output.embeddings[0].embedding

    def _get_entity_name(self, entity: Any) -> Optional[str]:
        """获取实体名称

        Args:
            entity: 实体（字符串或字典）

        Returns:
            实体名称
        """
        if isinstance(entity, dict):
            return entity.get("name")
        return str(entity) if entity else None

    def _append_first_event_id(self, event: Event) -> str:
        base = event.id or hash_summary(event.summary or uuid.uuid4().hex)
        ts = event.timestamp or event.last_active or int(time.time())
        return f"{base[:20]}_{ts}_{uuid.uuid4().hex[:6]}"
