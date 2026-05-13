# -*- coding: utf-8 -*-
"""Frontend-facing memory graph operations."""

from __future__ import annotations

from typing import Any, Optional
import time
import uuid

from .core.context import Context
from .core.event import Event
from .storage.graph_store import GraphStore


class MemoryGraphOps:
    """Provide stable CRUD-like operations for frontend and debugging flows."""

    def __init__(self, store: GraphStore, dynamic_engine: Optional[Any] = None):
        self.store = store
        self.dynamic_engine = dynamic_engine

    def write(
        self,
        item: Any,
        kind: str = "",
        evolve: bool = True,
        entity_ids: Optional[list[Any]] = None,
    ) -> dict[str, Any]:
        memory_kind = self._infer_kind(item, kind)
        if memory_kind == "event":
            event = self._coerce_event(item)
            existing = self.store.get_event(event.id) if event.id else None
            action = "updated" if existing else "created"
            if existing:
                event.created_at = event.created_at or existing.created_at
                event.embedding = event.embedding or existing.embedding
                self.store.update_event(event)
            else:
                self.store.save_event(event)
            linked_entities = self._upsert_event_entities(
                event_id=event.id,
                entity_ids=entity_ids,
                current_time=event.last_active or event.timestamp or int(time.time()),
            )
            if evolve and self.dynamic_engine:
                self.dynamic_engine.evolve_existing_events([event])
            stored = self.store.get_event(event.id) or event
            return {
                "kind": "event",
                "action": action,
                "entity_links": linked_entities,
                "item": self._serialize_event(stored),
            }

        if memory_kind == "context":
            context = self._coerce_context(item)
            existing = self.store.get_context(context.id) if context.id else None
            action = "updated" if existing else "created"
            if existing:
                context.created_at = context.created_at or existing.created_at
                context.embedding = context.embedding or existing.embedding
                self.store.update_context(context)
            else:
                self.store.save_context(context)
            stored = self.store.get_context(context.id) or context
            return {
                "kind": "context",
                "action": action,
                "item": self._serialize_context(stored),
            }

        raise ValueError(f"Unsupported write kind: {memory_kind}")

    def remove(
        self,
        memory_id: str,
        kind: str = "event",
        hard_delete: bool = False,
        removed_at: Optional[int] = None,
    ) -> dict[str, Any]:
        ts = int(removed_at or time.time())
        memory_kind = kind.strip().lower() or "event"
        if memory_kind == "event":
            before = self.store.get_event(memory_id)
            if hard_delete:
                self.store.delete_event(memory_id)
                after = None
                action = "deleted"
            else:
                self.store.archive_event(memory_id, ts)
                after = self.store.get_event(memory_id)
                action = "archived"
            return {
                "kind": "event",
                "action": action,
                "memory_id": memory_id,
                "before": self._serialize_event(before) if before else None,
                "after": self._serialize_event(after) if after else None,
            }

        if memory_kind == "context":
            before = self.store.get_context(memory_id)
            if hard_delete:
                self.store.delete_context(memory_id)
                after = None
                action = "deleted"
            else:
                self.store.archive_context(memory_id, ts)
                after = self.store.get_context(memory_id)
                action = "archived"
            return {
                "kind": "context",
                "action": action,
                "memory_id": memory_id,
                "before": self._serialize_context(before) if before else None,
                "after": self._serialize_context(after) if after else None,
            }

        raise ValueError(f"Unsupported remove kind: {memory_kind}")

    def merge_event(
        self,
        canonical_event_id: str,
        merged_event_id: str,
        merged_at: Optional[int] = None,
        similarity_score: float = 1.0,
        merge_reason: str = "manual_merge",
    ) -> dict[str, Any]:
        if not self.dynamic_engine:
            raise RuntimeError("Dynamic evolution engine is required for event merging")
        result = self.dynamic_engine.merge_events(
            canonical_event_id=canonical_event_id,
            merged_event_id=merged_event_id,
            merged_at=merged_at,
            similarity_score=similarity_score,
            merge_reason=merge_reason,
        )
        result["canonical_event"] = self._serialize_event(self.store.get_event(canonical_event_id))
        result["merged_event"] = self._serialize_event(self.store.get_event(merged_event_id))
        return result

    def merge_context(
        self,
        canonical_context_id: str,
        merged_context_id: str,
        merged_at: Optional[int] = None,
        rewrite_strategy: str = "rewrite",
    ) -> dict[str, Any]:
        if not self.dynamic_engine:
            raise RuntimeError("Dynamic evolution engine is required for context merging")
        result = self.dynamic_engine.merge_contexts(
            canonical_context_id=canonical_context_id,
            merged_context_id=merged_context_id,
            merged_at=merged_at,
            rewrite_strategy=rewrite_strategy,
        )
        result["canonical_context"] = self._serialize_context(self.store.get_context(canonical_context_id))
        result["merged_context"] = self._serialize_context(self.store.get_context(merged_context_id))
        return result

    # ==================== 注册实体（重要实体）算法接口 ====================

    def register_entity(
        self,
        entity_id: str,
        description: str,
        entity_type: str = "UNKNOWN",
        aliases: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """注册一个重要实体。

        - 若 id 不存在：创建注册节点；
        - 若已存在抽取节点：原地晋升为注册实体，并对该 id 触发一次回扫
          （把同名抽取节点上已有的 INVOLVES 边合并到注册节点本身，
           这里 canonical_id 与 merged_id 相同——表现为"复用同 id 的晋升"，
           不写 ENTITY_MERGE_TRACE）；
        - 若已是注册节点：等价于 update_entity。

        description 同步生成 description_embedding；裸名也生成 embedding
        以保留既有检索路径行为。
        """
        eid = str(entity_id or "").strip()
        if not eid:
            raise ValueError("entity_id is required")
        desc = str(description or "")
        etype = (entity_type or "UNKNOWN").strip() or "UNKNOWN"
        alias_list = [str(a).strip() for a in (aliases or []) if str(a).strip()]

        # 计算 embeddings
        embedding_client = getattr(self.store, "embedding_client", None)
        description_embedding: Optional[list[float]] = None
        name_embedding: Optional[list[float]] = None
        if embedding_client is not None and hasattr(embedding_client, "get_embedding"):
            try:
                description_embedding = embedding_client.get_embedding(
                    self._build_registration_embedding_text(eid, etype, desc)
                )
            except Exception:
                description_embedding = None
            try:
                name_embedding = embedding_client.get_embedding(eid)
            except Exception:
                name_embedding = None

        ts = int(time.time())
        result = self.store.register_entity_node(
            entity_id=eid,
            entity_type=etype,
            description=desc,
            description_embedding=description_embedding,
            aliases=alias_list,
            metadata=metadata or {},
            created_at=ts,
            name_embedding=name_embedding,
        )

        # 别名命中的旧抽取节点合并：把所有"目标节点 != eid 且是 alias 的抽取节点"
        # 合并到 eid。这覆盖了：用户用新 id 注册，aliases 中包含一个早已被抽取的旧名。
        if self.dynamic_engine is not None:
            for alias in alias_list:
                if alias == eid:
                    continue
                existing_alias_node = self._get_entity_safe(alias)
                if (
                    existing_alias_node is not None
                    and existing_alias_node.status == "active"
                    and existing_alias_node.id != eid
                ):
                    try:
                        self.dynamic_engine.merge_entity(
                            canonical_id=eid,
                            merged_id=existing_alias_node.id,
                            merge_reason="exact_match",
                            similarity_score=1.0,
                            merged_at=ts,
                        )
                    except Exception:
                        # 容错：单个别名合并失败不影响注册主流程
                        pass

        stored = self._get_entity_safe(eid)
        return {
            "action": result.get("mode", "registered"),
            "existed_as_extracted": bool(result.get("existed_as_extracted", False)),
            "entity": stored.to_serializable() if stored else None,
        }

    def update_entity(
        self,
        entity_id: str,
        *,
        description: Optional[str] = None,
        entity_type: Optional[str] = None,
        add_aliases: Optional[list[str]] = None,
        remove_aliases: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """更新注册实体属性。description 变更时重算 description_embedding。"""
        eid = str(entity_id or "").strip()
        if not eid:
            raise ValueError("entity_id is required")
        existing = self._get_entity_safe(eid)
        if existing is None or not existing.registered:
            raise ValueError(f"Registered entity not found: {eid}")

        ts = int(time.time())
        description_embedding: Optional[list[float]] = None
        if description is not None:
            embedding_client = getattr(self.store, "embedding_client", None)
            new_desc = str(description or "")
            new_type = (entity_type or existing.type or "UNKNOWN")
            if embedding_client is not None and hasattr(embedding_client, "get_embedding"):
                try:
                    description_embedding = embedding_client.get_embedding(
                        self._build_registration_embedding_text(eid, new_type, new_desc)
                    )
                except Exception:
                    description_embedding = None

        self.store.update_entity_attributes(
            eid,
            description=description,
            description_embedding=description_embedding if description is not None else None,
            entity_type=entity_type,
            add_aliases=add_aliases,
            remove_aliases=remove_aliases,
            metadata=metadata,
            updated_at=ts,
        )

        stored = self._get_entity_safe(eid)
        return {
            "action": "updated",
            "entity": stored.to_serializable() if stored else None,
        }

    def get_registered_entity(self, entity_id: str) -> Optional[dict[str, Any]]:
        eid = str(entity_id or "").strip()
        if not eid:
            return None
        try:
            ent = self.store.get_registered_entity(eid)
        except NotImplementedError:
            return None
        if ent is None:
            return None
        return ent.to_serializable()

    def list_registered_entities(self) -> list[dict[str, Any]]:
        """列出所有注册实体（不含 embedding 大数组）。"""
        lister = getattr(self.store, "list_registered_entities_with_embeddings", None)
        if not callable(lister):
            return []
        try:
            ents = lister() or []
        except NotImplementedError:
            return []
        return [e.to_serializable() for e in ents if e is not None]

    def unregister_entity(self, entity_id: str) -> Optional[dict[str, Any]]:
        """物理删除一个注册实体（含所有 HAS_PATTERN / INVOLVES 等关系）。

        仅用于注册流程的回滚 —— **不会触发 dynamic_engine**，也不会写
        ENTITY_MERGE_TRACE。调用方需自行确保该实体的所有 pattern 已先被回滚。

        Returns: 被删除实体的序列化快照；若实体不存在或不是注册节点，返回 None。
        """
        eid = str(entity_id or "").strip()
        if not eid:
            raise ValueError("entity_id is required")
        remover = getattr(self.store, "unregister_entity_node", None)
        if not callable(remover):
            raise NotImplementedError("unregister_entity_node is not implemented")
        existing = remover(eid)
        if existing is None:
            return None
        return existing.to_serializable()

    # ==================== 注册实体 Pattern 接口 ====================

    def create_entity_pattern(
        self,
        entity_id: str,
        content: str,
        pattern_type: str = "preference",
        metadata: Optional[dict[str, Any]] = None,
        pattern_id: Optional[str] = None,
    ) -> dict[str, Any]:
        eid = str(entity_id or "").strip()
        if not eid:
            raise ValueError("entity_id is required")
        if self.get_registered_entity(eid) is None:
            raise ValueError(f"Registered entity not found: {eid}")
        text = str(content or "").strip()
        if not text:
            raise ValueError("content is required")
        ptype = str(pattern_type or "preference").strip() or "preference"
        ts = int(time.time())
        creator = getattr(self.store, "create_entity_pattern", None)
        if not callable(creator):
            raise NotImplementedError("create_entity_pattern is not implemented")
        pattern = creator(
            entity_id=eid,
            content=text,
            pattern_type=ptype,
            metadata=metadata or {},
            created_at=ts,
            pattern_id=pattern_id,
        )
        return {"action": "created", "pattern": pattern}

    def get_entity_pattern(self, entity_id: str, pattern_id: str) -> Optional[dict[str, Any]]:
        eid = str(entity_id or "").strip()
        pid = str(pattern_id or "").strip()
        if not eid or not pid:
            return None
        getter = getattr(self.store, "get_entity_pattern", None)
        if not callable(getter):
            return None
        try:
            return getter(eid, pid)
        except NotImplementedError:
            return None

    def list_entity_patterns(
        self,
        entity_id: str,
        query: str = "",
        limit: int = 100,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        eid = str(entity_id or "").strip()
        if not eid:
            raise ValueError("entity_id is required")
        if self.get_registered_entity(eid) is None:
            raise ValueError(f"Registered entity not found: {eid}")
        lister = getattr(self.store, "list_entity_patterns", None)
        if not callable(lister):
            return []
        try:
            return lister(
                eid,
                query=str(query or ""),
                limit=int(limit or 100),
                include_inactive=include_inactive,
            )
        except NotImplementedError:
            return []

    def update_entity_pattern(
        self,
        entity_id: str,
        pattern_id: str,
        *,
        content: Optional[str] = None,
        pattern_type: Optional[str] = None,
        status: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        eid = str(entity_id or "").strip()
        pid = str(pattern_id or "").strip()
        if not eid:
            raise ValueError("entity_id is required")
        if not pid:
            raise ValueError("pattern_id is required")
        existing = self.get_entity_pattern(eid, pid)
        if existing is None:
            raise ValueError(f"Entity pattern not found: {pid}")
        if content is not None and not str(content or "").strip():
            raise ValueError("content cannot be empty")
        new_content = str(content).strip() if content is not None else existing["content"]
        new_type = str(pattern_type or "").strip() if pattern_type is not None else existing["pattern_type"]
        new_status = str(status or "").strip() if status is not None else existing["status"]
        if new_status not in {"active", "archived"}:
            raise ValueError("status must be active or archived")
        ts = int(time.time())
        updater = getattr(self.store, "update_entity_pattern", None)
        if not callable(updater):
            raise NotImplementedError("update_entity_pattern is not implemented")
        pattern = updater(
            eid,
            pid,
            content=new_content if content is not None else None,
            pattern_type=new_type if pattern_type is not None else None,
            status=new_status if status is not None else None,
            metadata=metadata,
            updated_at=ts,
        )
        if pattern is None:
            raise ValueError(f"Entity pattern not found: {pid}")
        return {"action": "updated", "pattern": pattern}

    def delete_entity_pattern(
        self,
        entity_id: str,
        pattern_id: str,
        hard_delete: bool = False,
    ) -> dict[str, Any]:
        eid = str(entity_id or "").strip()
        pid = str(pattern_id or "").strip()
        if not eid:
            raise ValueError("entity_id is required")
        if not pid:
            raise ValueError("pattern_id is required")
        deleter = getattr(self.store, "delete_entity_pattern", None)
        if not callable(deleter):
            raise NotImplementedError("delete_entity_pattern is not implemented")
        pattern = deleter(
            eid,
            pid,
            deleted_at=int(time.time()),
            hard_delete=hard_delete,
        )
        if pattern is None:
            raise ValueError(f"Entity pattern not found: {pid}")
        return {
            "action": "deleted" if hard_delete else "archived",
            "pattern": pattern,
        }

    def _get_entity_safe(self, entity_id: str) -> Optional[Any]:
        getter = getattr(self.store, "get_entity", None)
        if not callable(getter):
            return None
        try:
            return getter(entity_id)
        except NotImplementedError:
            return None
        except Exception:
            return None

    def _build_registration_embedding_text(
        self,
        entity_id: str,
        entity_type: str,
        description: str,
    ) -> str:
        return f"{entity_id} ({entity_type}): {description}".strip()

    def query(
        self,
        text: str = "",
        limit: int = 20,
        include_graph: bool = True,
        include_inactive: bool = False,
    ) -> dict[str, Any]:
        statuses = None if include_inactive else ["active"]
        events = self.store.list_events(limit=limit, query=text, statuses=statuses)
        contexts = self.store.list_contexts(limit=limit, query=text, statuses=statuses)

        result = {
            "query": text,
            "stats": self.store.get_stats(),
            "events": [self._serialize_event(event) for event in events],
            "contexts": [self._serialize_context(context) for context in contexts],
        }
        if include_graph:
            result["edges"] = self._build_edge_bundle(limit=limit * 5, statuses=statuses)
        return result

    def snapshot(
        self,
        limit: int = 20,
        include_inactive: bool = False,
        text: str = "",
    ) -> dict[str, Any]:
        return self.query(
            text=text,
            limit=limit,
            include_graph=True,
            include_inactive=include_inactive,
        )

    def auto_merge(
        self,
        scope: str = "all",
        strategy: str = "auto",
        dry_run: bool = False,
        max_pairs: int = 10,
        focus_event_ids: Optional[list[str]] = None,
        event_same_scope_only: bool = False,
    ) -> dict[str, Any]:
        if not self.dynamic_engine:
            raise RuntimeError("Dynamic evolution engine is required for auto merge")
        result = self.dynamic_engine.auto_merge(
            scope=scope,
            strategy=strategy,
            dry_run=dry_run,
            max_pairs=max_pairs,
            focus_event_ids=focus_event_ids,
            event_same_scope_only=event_same_scope_only,
        )
        result["snapshot"] = self.snapshot(limit=max(20, max_pairs * 8), include_inactive=True)
        return result

    def _build_edge_bundle(
        self,
        limit: int,
        statuses: Optional[list[str]],
    ) -> dict[str, list[dict[str, Any]]]:
        return {
            "next": [],
            "event_event": self.store.list_event_event_edges(
                limit=limit,
                event_statuses=statuses,
            ),
            "event_context": self.store.list_event_context_edges(
                limit=limit,
                event_statuses=statuses,
                context_statuses=statuses,
            ),
        }

    def _infer_kind(self, item: Any, kind: str) -> str:
        if kind:
            return kind.strip().lower()
        if isinstance(item, Event):
            return "event"
        if isinstance(item, Context):
            return "context"
        if isinstance(item, dict):
            if "context_type" in item or "description" in item:
                return "context"
            if "subtype" in item and "summary" in item and "participants" not in item and "action" not in item:
                return "context"
            return "event"
        raise ValueError("Unable to infer memory kind")

    def _coerce_event(self, item: Any) -> Event:
        if isinstance(item, Event):
            event = item
        elif isinstance(item, dict):
            event = Event(
                id=str(item.get("id", "") or ""),
                summary=str(item.get("summary", "") or ""),
                action=str(item.get("action", "") or ""),
                causality=str(item.get("causality", "") or ""),
                time_range=dict(item.get("time_range", {}) or {}),
                timestamp=int(item.get("timestamp", 0) or 0),
                last_active=int(item.get("last_active", item.get("timestamp", 0)) or 0),
                created_at=int(item.get("created_at", item.get("timestamp", 0)) or 0),
                updated_at=int(item.get("updated_at", item.get("last_active", item.get("timestamp", 0))) or 0),
                valid_from=int(item.get("valid_from", item.get("timestamp", 0)) or 0),
                valid_to=item.get("valid_to"),
                participants=list(item.get("participants", []) or []),
                payload=dict(item.get("payload", {}) or {}),
                evidence=list(item.get("evidence", []) or []),
                status=str(item.get("status", "active") or "active"),
                support_count=int(item.get("support_count", 1) or 1),
                embedding=item.get("embedding"),
            )
        else:
            raise ValueError("Unsupported event payload")

        now = int(time.time())
        if event.timestamp <= 0:
            event.timestamp = event.last_active or now
        if event.last_active <= 0:
            event.last_active = event.timestamp or now
        if event.created_at <= 0:
            event.created_at = event.timestamp or now
        if event.updated_at <= 0:
            event.updated_at = event.last_active or event.timestamp or now
        if event.valid_from <= 0:
            event.valid_from = event.timestamp or now
        event.status = event.status or "active"
        return event

    def _coerce_context(self, item: Any) -> Context:
        if isinstance(item, Context):
            context = item
        elif isinstance(item, dict):
            context = Context(
                id=str(item.get("id", "") or ""),
                context_type=str(item.get("context_type", "context") or "context"),
                subtype=str(item.get("subtype", "situation") or "situation"),
                summary=str(item.get("summary", "") or ""),
                description=str(item.get("description", "") or ""),
                confidence=float(item.get("confidence", 0.6) or 0.6),
                support_count=int(item.get("support_count", 1) or 1),
                created_at=int(item.get("created_at", 0) or 0),
                updated_at=int(item.get("updated_at", 0) or 0),
                valid_from=int(item.get("valid_from", 0) or 0),
                valid_to=item.get("valid_to"),
                last_seen_at=int(item.get("last_seen_at", 0) or 0),
                status=str(item.get("status", "active") or "active"),
                source_refs=list(item.get("source_refs", []) or []),
                merged_from=list(item.get("merged_from", []) or []),
                embedding=item.get("embedding"),
            )
        else:
            raise ValueError("Unsupported context payload")

        now = int(time.time())
        if not context.id:
            context.id = f"ctx_manual_{uuid.uuid4().hex[:12]}"
        if context.created_at <= 0:
            context.created_at = now
        if context.updated_at <= 0:
            context.updated_at = context.created_at
        if context.valid_from <= 0:
            context.valid_from = context.created_at
        if context.last_seen_at <= 0:
            context.last_seen_at = context.updated_at
        context.status = context.status or "active"
        return context

    def _upsert_event_entities(
        self,
        event_id: str,
        entity_ids: Optional[list[Any]],
        current_time: int,
    ) -> int:
        if not entity_ids:
            return 0
        linked = 0
        for entity in entity_ids:
            entity_name = ""
            entity_type = "UNKNOWN"
            if isinstance(entity, dict):
                entity_name = str(
                    entity.get("id")
                    or entity.get("name")
                    or entity.get("value")
                    or ""
                ).strip()
                entity_type = str(entity.get("type", "UNKNOWN") or "UNKNOWN")
            else:
                entity_name = str(entity or "").strip()
            if not entity_name:
                continue
            self.store.ensure_entity(entity_name, entity_type)
            relation = self.store.get_involves_relation(event_id, entity_name)
            if relation:
                relation.c_valid += 1
                relation.t_valid = current_time
                self.store.update_involves_relation(relation)
            else:
                self.store.create_involves_relation(
                    event_id=event_id,
                    entity_id=entity_name,
                    t_created=current_time,
                    t_valid=current_time,
                    c_valid=1,
                )
            linked += 1
        return linked

    def _serialize_event(self, event: Optional[Event]) -> Optional[dict[str, Any]]:
        if event is None:
            return None
        return {
            "id": event.id,
            "summary": event.summary,
            "action": event.action,
            "causality": event.causality,
            "time_range": event.time_range,
            "timestamp": event.timestamp,
            "last_active": event.last_active,
            "created_at": event.created_at,
            "updated_at": event.updated_at,
            "valid_from": event.valid_from,
            "valid_to": event.valid_to,
            "participants": event.participants,
            "payload": event.payload,
            "evidence": event.evidence,
            "status": event.status,
            "support_count": event.support_count,
            "entity_ids": self.store.get_event_entities(event.id),
            "context_ids": [context.id for context in self.store.get_event_contexts(event.id)],
            "merge_traces": self.store.list_event_merge_traces(event.id),
        }

    def _serialize_context(self, context: Optional[Context]) -> Optional[dict[str, Any]]:
        if context is None:
            return None
        return {
            "id": context.id,
            "context_type": context.context_type,
            "subtype": context.subtype,
            "summary": context.summary,
            "description": context.description,
            "confidence": context.confidence,
            "support_count": context.support_count,
            "created_at": context.created_at,
            "updated_at": context.updated_at,
            "valid_from": context.valid_from,
            "valid_to": context.valid_to,
            "last_seen_at": context.last_seen_at,
            "status": context.status,
            "source_refs": context.source_refs,
            "merged_from": context.merged_from,
        }
