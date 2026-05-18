# -*- coding: utf-8 -*-
"""KuzuStore - Kuzu 图数据库实现

实现 GraphStore 接口的 Kuzu 具体实现。
"""

import os
import re
import time
import uuid
from typing import Any, Optional

import kuzu

from ..core.episode import Episode
from ..core.event import Event, EventRelation
from ..core.context import Context
from ..core.entity import Entity
from ..utils import safe_json_dumps, safe_json_loads
from .graph_store import GraphStore


class KuzuStore(GraphStore):
    """Kuzu 图数据库实现

    职责：实现 GraphStore 接口，提供 Kuzu 数据库的具体操作。
    """

    def __init__(self, db_path: str, embedding_client=None, embedding_dim: int = 1024):
        """初始化 Kuzu 存储

        Args:
            db_path: 数据库文件路径
            embedding_client: 嵌入向量客户端（用于实体嵌入生成）
            embedding_dim: 嵌入向量维度（需与 embedding 模型输出一致）
        """
        self.db_path = self._normalize_db_path(db_path)
        self.embedding_client = embedding_client
        self.embedding_dim = embedding_dim

        # 打开连接
        print(f"📁 Using Kuzu DB file: {self.db_path}")
        self.db = kuzu.Database(self.db_path)
        self.conn = kuzu.Connection(self.db)
        self._closed = False

        # 初始化 Schema
        self._init_schema()

    def close(self) -> None:
        """释放 Kuzu 连接与数据库句柄

        用于多库 LRU 池淘汰场景：归还文件句柄、避免长时间运行下耗尽 inode/FD。
        幂等：重复调用安全。
        """
        if getattr(self, "_closed", False):
            return
        import gc

        try:
            conn = getattr(self, "conn", None)
            if conn is not None and hasattr(conn, "close"):
                try:
                    conn.close()
                except Exception:
                    pass
        finally:
            self.conn = None
        try:
            db = getattr(self, "db", None)
            if db is not None and hasattr(db, "close"):
                try:
                    db.close()
                except Exception:
                    pass
        finally:
            self.db = None
        self._closed = True
        gc.collect()

    def _normalize_db_path(self, db_path: str) -> str:
        """标准化数据库路径"""
        db_path = os.path.expanduser(db_path)
        if db_path.endswith(os.sep) or (os.path.exists(db_path) and os.path.isdir(db_path)):
            db_path = os.path.join(db_path, "database.kz")
        parent = os.path.dirname(db_path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)
        return db_path

    def _init_schema(self) -> None:
        """初始化数据库 Schema"""
        dim = self.embedding_dim
        # 节点表
        self.conn.execute("""
            CREATE NODE TABLE IF NOT EXISTS Episode(
                id STRING,
                content STRING,
                timestamp INT64,
                PRIMARY KEY(id)
            )
        """)

        self.conn.execute(f"""
            CREATE NODE TABLE IF NOT EXISTS Event(
                id STRING,
                summary STRING,
                participants STRING,
                time_range STRING,
                action STRING,
                causality STRING,
                payload STRING,
                evidence STRING,
                timestamp INT64,
                last_active INT64,
                created_at INT64,
                updated_at INT64,
                valid_from INT64,
                valid_to INT64,
                status STRING,
                support_count INT64,
                embedding FLOAT[{dim}],
                PRIMARY KEY(id)
            )
        """)

        self.conn.execute(f"""
            CREATE NODE TABLE IF NOT EXISTS Entity(
                id STRING,
                type STRING,
                embedding FLOAT[{dim}],
                description STRING,
                description_embedding FLOAT[{dim}],
                aliases STRING,
                registered BOOLEAN,
                status STRING,
                canonical_id STRING,
                merged_from STRING,
                metadata STRING,
                created_at INT64,
                updated_at INT64,
                PRIMARY KEY(id)
            )
        """)

        self.conn.execute("""
            CREATE NODE TABLE IF NOT EXISTS User(
                id STRING,
                PRIMARY KEY(id)
            )
        """)
        self.conn.execute(f"""
            CREATE NODE TABLE IF NOT EXISTS Context(
                id STRING,
                context_type STRING,
                subtype STRING,
                summary STRING,
                description STRING,
                confidence DOUBLE,
                support_count INT64,
                created_at INT64,
                updated_at INT64,
                valid_from INT64,
                valid_to INT64,
                last_seen_at INT64,
                status STRING,
                source_refs STRING,
                merged_from STRING,
                embedding FLOAT[{dim}],
                PRIMARY KEY(id)
            )
        """)
        # Pattern v2：单文档 markdown 模型。
        # 若检测到旧 schema（无 entity_id 列）→ 先 drop edge + drop node 再重建。
        # 旧数据无迁移，按设计接受丢失。
        if self._pattern_table_is_legacy():
            for stmt in ("DROP TABLE HAS_PATTERN", "DROP TABLE Pattern"):
                try:
                    self.conn.execute(stmt)
                except Exception:
                    continue
        self.conn.execute("""
            CREATE NODE TABLE IF NOT EXISTS Pattern(
                id STRING,
                entity_id STRING,
                content STRING,
                status STRING,
                created_at INT64,
                updated_at INT64,
                PRIMARY KEY(id)
            )
        """)

        # 关系表
        self.conn.execute("""
            CREATE REL TABLE IF NOT EXISTS INVOLVES(
                FROM Event TO Entity,
                t_created INT64,
                t_expired INT64,
                t_valid INT64,
                t_invalid INT64,
                c_valid INT64
            )
        """)

        self.conn.execute("""
            CREATE REL TABLE IF NOT EXISTS EXTRACTED_FROM(
                FROM Event TO Episode
            )
        """)

        self.conn.execute("""
            CREATE REL TABLE IF NOT EXISTS PERMANENT_TRAIT(
                FROM User TO Event,
                t_created INT64
            )
        """)
        self.conn.execute("""
            CREATE REL TABLE IF NOT EXISTS IN_REL(
                FROM Event TO Context,
                confidence DOUBLE,
                weight DOUBLE,
                original_signal STRING,
                evidence_span STRING,
                created_at INT64,
                updated_at INT64,
                last_seen_at INT64
            )
        """)
        self.conn.execute("""
            CREATE REL TABLE IF NOT EXISTS EVENT_MERGE_TRACE(
                FROM Event TO Event,
                merge_reason STRING,
                similarity_score DOUBLE,
                merged_at INT64,
                strategy_version STRING
            )
        """)
        self.conn.execute("""
            CREATE REL TABLE IF NOT EXISTS ENTITY_MERGE_TRACE(
                FROM Entity TO Entity,
                merge_reason STRING,
                similarity_score DOUBLE,
                merged_at INT64,
                strategy_version STRING
            )
        """)
        self.conn.execute("""
            CREATE REL TABLE IF NOT EXISTS EVENT_RELATION(
                FROM Event TO Event,
                relation_type STRING,
                operation STRING,
                description STRING,
                confidence DOUBLE,
                evidence_span STRING,
                value_before STRING,
                value_after STRING,
                recall_channel STRING,
                recall_score DOUBLE,
                source_episode_id STRING,
                source_session_id STRING,
                created_at INT64,
                updated_at INT64
            )
        """)
        self.conn.execute("""
            CREATE REL TABLE IF NOT EXISTS HAS_PATTERN(
                FROM Entity TO Pattern
            )
        """)

        # Best-effort 迁移
        self._run_migrations()

    def _pattern_table_is_legacy(self) -> bool:
        """探测 Pattern 表是否为旧 schema（不含 entity_id 列）。

        旧 schema：(id, content, pattern_type, status, metadata, created_at, updated_at)
        新 schema：(id, entity_id, content, status, created_at, updated_at)

        如果表不存在或查询失败返回 False（让 CREATE TABLE IF NOT EXISTS 走正常路径）。
        """
        try:
            self.conn.execute("MATCH (p:Pattern) RETURN p.entity_id LIMIT 1")
            return False  # 新 schema 已可读 entity_id 列
        except Exception as exc:
            # 表存在但读不到 entity_id 列 → 旧 schema；表不存在则报"table not found"。
            msg = str(exc).lower()
            if "entity_id" in msg or "property" in msg or "field" in msg:
                return True
            return False

    def _run_migrations(self) -> None:
        """运行数据库迁移"""
        dim = self.embedding_dim
        migrations = [
            "ALTER TABLE Event ADD participants STRING",
            "ALTER TABLE Event ADD time_range STRING",
            "ALTER TABLE Event ADD action STRING",
            "ALTER TABLE Event ADD causality STRING",
            "ALTER TABLE Event ADD payload STRING",
            "ALTER TABLE Event ADD evidence STRING",
            "ALTER TABLE Event ADD timestamp INT64",
            "ALTER TABLE Event ADD last_active INT64",
            "ALTER TABLE Event ADD created_at INT64",
            "ALTER TABLE Event ADD updated_at INT64",
            "ALTER TABLE Event ADD valid_from INT64",
            "ALTER TABLE Event ADD valid_to INT64",
            "ALTER TABLE Event ADD status STRING",
            "ALTER TABLE Event ADD support_count INT64",
            f"ALTER TABLE Entity ADD embedding FLOAT[{dim}]",
            "ALTER TABLE Entity ADD description STRING",
            f"ALTER TABLE Entity ADD description_embedding FLOAT[{dim}]",
            "ALTER TABLE Entity ADD aliases STRING",
            "ALTER TABLE Entity ADD registered BOOLEAN",
            "ALTER TABLE Entity ADD status STRING",
            "ALTER TABLE Entity ADD canonical_id STRING",
            "ALTER TABLE Entity ADD merged_from STRING",
            "ALTER TABLE Entity ADD metadata STRING",
            "ALTER TABLE Entity ADD created_at INT64",
            "ALTER TABLE Entity ADD updated_at INT64",
            "ALTER TABLE ENTITY_MERGE_TRACE ADD merge_reason STRING",
            "ALTER TABLE ENTITY_MERGE_TRACE ADD similarity_score DOUBLE",
            "ALTER TABLE ENTITY_MERGE_TRACE ADD merged_at INT64",
            "ALTER TABLE ENTITY_MERGE_TRACE ADD strategy_version STRING",
            "ALTER TABLE INVOLVES ADD t_expired INT64",
            "ALTER TABLE INVOLVES ADD t_valid INT64",
            "ALTER TABLE INVOLVES ADD t_invalid INT64",
            "ALTER TABLE INVOLVES ADD c_valid INT64",
            "ALTER TABLE Context ADD description STRING",
            "ALTER TABLE Context ADD confidence DOUBLE",
            "ALTER TABLE Context ADD support_count INT64",
            "ALTER TABLE Context ADD created_at INT64",
            "ALTER TABLE Context ADD updated_at INT64",
            "ALTER TABLE Context ADD valid_from INT64",
            "ALTER TABLE Context ADD valid_to INT64",
            "ALTER TABLE Context ADD last_seen_at INT64",
            "ALTER TABLE Context ADD status STRING",
            "ALTER TABLE Context ADD source_refs STRING",
            "ALTER TABLE Context ADD merged_from STRING",
            f"ALTER TABLE Context ADD embedding FLOAT[{dim}]",
            "ALTER TABLE IN_REL ADD original_signal STRING",
            "ALTER TABLE IN_REL ADD evidence_span STRING",
            "ALTER TABLE IN_REL ADD confidence DOUBLE",
            "ALTER TABLE IN_REL ADD weight DOUBLE",
            "ALTER TABLE IN_REL ADD created_at INT64",
            "ALTER TABLE IN_REL ADD updated_at INT64",
            "ALTER TABLE IN_REL ADD last_seen_at INT64",
            "ALTER TABLE EVENT_MERGE_TRACE ADD merge_reason STRING",
            "ALTER TABLE EVENT_MERGE_TRACE ADD similarity_score DOUBLE",
            "ALTER TABLE EVENT_MERGE_TRACE ADD merged_at INT64",
            "ALTER TABLE EVENT_MERGE_TRACE ADD strategy_version STRING",
            "ALTER TABLE EVENT_RELATION ADD relation_type STRING",
            "ALTER TABLE EVENT_RELATION ADD operation STRING",
            "ALTER TABLE EVENT_RELATION ADD description STRING",
            "ALTER TABLE EVENT_RELATION ADD confidence DOUBLE",
            "ALTER TABLE EVENT_RELATION ADD evidence_span STRING",
            "ALTER TABLE EVENT_RELATION ADD value_before STRING",
            "ALTER TABLE EVENT_RELATION ADD value_after STRING",
            "ALTER TABLE EVENT_RELATION ADD recall_channel STRING",
            "ALTER TABLE EVENT_RELATION ADD recall_score DOUBLE",
            "ALTER TABLE EVENT_RELATION ADD source_episode_id STRING",
            "ALTER TABLE EVENT_RELATION ADD source_session_id STRING",
            "ALTER TABLE EVENT_RELATION ADD created_at INT64",
            "ALTER TABLE EVENT_RELATION ADD updated_at INT64",
        ]

        for stmt in migrations:
            try:
                self.conn.execute(stmt)
            except Exception:
                continue

    def _event_columns(self) -> list[str]:
        return [
            "id", "summary", "action", "causality", "time_range",
            "last_active", "participants", "evidence",
            "embedding", "timestamp", "created_at", "updated_at",
            "valid_from", "valid_to", "status", "support_count", "payload",
        ]

    def _event_select_clause(self, alias: str = "e") -> str:
        return f"""
            {alias}.id, {alias}.summary, {alias}.action, {alias}.causality, {alias}.time_range,
            {alias}.last_active, {alias}.participants, {alias}.evidence,
            {alias}.embedding, {alias}.timestamp, {alias}.created_at, {alias}.updated_at,
            {alias}.valid_from, {alias}.valid_to, {alias}.status,
            {alias}.support_count, {alias}.payload
        """

    def _row_to_event(self, row: list[Any]) -> Event:
        return Event(
            id=row[0],
            summary=row[1],
            action=row[2] or "",
            causality=row[3] or "",
            time_range=safe_json_loads(row[4], {}),
            last_active=row[5] or 0,
            participants=safe_json_loads(row[6], []),
            evidence=safe_json_loads(row[7], []),
            embedding=list(row[8]) if row[8] else None,
            timestamp=row[9] or 0,
            created_at=row[10] or 0,
            updated_at=row[11] or 0,
            valid_from=row[12] or 0,
            valid_to=row[13],
            status=row[14] or "active",
            support_count=int(row[15] or 1),
            payload=safe_json_loads(row[16], {}),
        )

    def _tokenize_text(self, text: str) -> set[str]:
        raw = str(text or "").lower()
        tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_]+", raw))
        compact = re.sub(r"\s+", "", raw)
        if len(compact) >= 2:
            tokens.update(compact[idx: idx + 2] for idx in range(len(compact) - 1))
        elif compact:
            tokens.add(compact)
        return {token for token in tokens if token}

    def _text_similarity_score(
        self,
        text: str,
        query: str,
        query_entities: list[str],
    ) -> float:
        text_tokens = self._tokenize_text(text)
        query_tokens = self._tokenize_text(query)
        entity_tokens = set()
        for entity in query_entities:
            entity_tokens.update(self._tokenize_text(entity))
        lexical = 0.0
        if text_tokens and query_tokens:
            lexical = len(text_tokens & query_tokens) / len(text_tokens | query_tokens)
        entity_hits = len(text_tokens & entity_tokens) if entity_tokens else 0
        return lexical + 0.25 * entity_hits

    # ==================== Episode 操作 ====================

    def save_episode(self, episode: Episode) -> None:
        """保存Episode"""
        fields = episode.to_db_fields()
        self.conn.execute(
            "CREATE (:Episode {id: $id, content: $content, timestamp: $timestamp})",
            {"id": fields["id"], "content": fields["content"], "timestamp": fields["timestamp"]},
        )

    def get_episode(self, episode_id: str) -> Optional[Episode]:
        """获取Episode"""
        resp = self.conn.execute(
            "MATCH (e:Episode {id: $id}) RETURN e.id, e.content, e.timestamp",
            {"id": episode_id},
        )
        if resp.has_next():
            row = resp.get_next()
            return Episode(
                id=row[0],
                content=row[1],
                timestamp=row[2],
            )
        return None

    def delete_expired_episodes(self, current_time: int, ttl: int) -> int:
        """删除过期Episode"""
        threshold = current_time - ttl

        # 统计数量
        count_resp = self.conn.execute(
            "MATCH (e:Episode) WHERE e.timestamp < $threshold RETURN count(*)",
            {"threshold": threshold},
        )
        count = count_resp.get_next()[0] if count_resp.has_next() else 0

        # 删除
        self.conn.execute(
            "MATCH (e:Episode) WHERE e.timestamp < $threshold DETACH DELETE e",
            {"threshold": threshold},
        )

        return count

    # ==================== Event 操作 ====================

    def save_event(self, event: Event) -> None:
        """保存Event"""
        fields = event.to_db_fields()
        self.conn.execute(
            """
            CREATE (:Event {
                id: $id,
                summary: $summary,
                participants: $participants,
                time_range: $time_range,
                action: $action,
                causality: $causality,
                payload: $payload,
                evidence: $evidence,
                timestamp: $timestamp,
                last_active: $last_active,
                created_at: $created_at,
                updated_at: $updated_at,
                valid_from: $valid_from,
                valid_to: $valid_to,
                status: $status,
                support_count: $support_count,
                embedding: $embedding
            })
            """,
            fields,
        )

    def save_events_batch(self, events: list[Event]) -> None:
        """批量保存 Event 节点。"""
        if not events:
            return
        if any(event.embedding is None for event in events):
            for event in events:
                self.save_event(event)
            return

        rows: list[dict[str, Any]] = []
        for event in events:
            fields = event.to_db_fields()
            fields["valid_to"] = int(fields["valid_to"]) if fields["valid_to"] is not None else -1
            rows.append(fields)

        self.conn.execute(
            """
            WITH $rows AS rows
            UNWIND rows AS row
            CREATE (:Event {
                id: row.id,
                summary: row.summary,
                participants: row.participants,
                time_range: row.time_range,
                action: row.action,
                causality: row.causality,
                payload: row.payload,
                evidence: row.evidence,
                timestamp: row.timestamp,
                last_active: row.last_active,
                created_at: row.created_at,
                updated_at: row.updated_at,
                valid_from: row.valid_from,
                valid_to: CASE WHEN row.valid_to < 0 THEN NULL ELSE row.valid_to END,
                status: row.status,
                support_count: row.support_count,
                embedding: row.embedding
            })
            """,
            {"rows": rows},
        )

    def get_event(self, event_id: str) -> Optional[Event]:
        """获取Event"""
        resp = self.conn.execute(
            """
            MATCH (e:Event {id: $id})
            RETURN e.id, e.summary, e.action, e.causality, e.time_range,
                   e.last_active, e.participants, e.evidence, e.embedding,
                   e.timestamp, e.created_at, e.updated_at, e.valid_from, e.valid_to,
                   e.status, e.support_count, e.payload
            """,
            {"id": event_id},
        )
        if resp.has_next():
            return self._row_to_event(list(resp.get_next()))
        return None

    def update_event(self, event: Event) -> None:
        """更新Event"""
        fields = event.to_db_fields()
        self.conn.execute(
            """
            MATCH (e:Event {id: $id})
            SET e.summary = $summary,
                e.participants = $participants,
                e.time_range = $time_range,
                e.action = $action,
                e.causality = $causality,
                e.payload = $payload,
                e.evidence = $evidence,
                e.timestamp = $timestamp,
                e.last_active = $last_active,
                e.created_at = $created_at,
                e.updated_at = $updated_at,
                e.valid_from = $valid_from,
                e.valid_to = $valid_to,
                e.status = $status,
                e.support_count = $support_count,
                e.embedding = $embedding
            """,
            fields,
        )

    def get_events_by_entities(self, entities: list[str]) -> list[Event]:
        """根据实体获取关联的事件"""
        query = """
            MATCH (e:Event)-[:INVOLVES]->(en:Entity)
            WHERE en.id IN $entities
            RETURN DISTINCT e.id, e.summary, e.action, e.causality, e.time_range,
                   e.last_active, e.participants, e.evidence, e.embedding,
                   e.timestamp, e.created_at, e.updated_at, e.valid_from, e.valid_to,
                   e.status, e.support_count, e.payload
        """
        resp = self.conn.execute(query, {"entities": entities})

        events = []
        while resp.has_next():
            events.append(self._row_to_event(list(resp.get_next())))
        return events

    def get_all_events_with_entities(self) -> list[dict[str, Any]]:
        """获取所有事件及其关联实体"""
        query = """
            MATCH (e:Event)-[r:INVOLVES]->(en:Entity)
            RETURN e.id, e.summary, e.embedding, e.action, e.last_active,
                   collect(en.id), e.status, e.timestamp
        """
        resp = self.conn.execute(query)

        events = []
        while resp.has_next():
            row = resp.get_next()
            events.append({
                "id": row[0],
                "summary": row[1],
                "embedding": list(row[2]) if row[2] else None,
                "action": row[3] or "",
                "last_active": row[4],
                "entities": row[5],
                "status": row[6] or "active",
                "timestamp": row[7] or 0,
            })
        return events

    # ==================== Entity 操作 ====================

    # 仅在 SELECT 这些非主键字段时使用；主键 id 单独处理
    _ENTITY_NON_KEY_COLUMNS = [
        "type",
        "embedding",
        "description",
        "description_embedding",
        "aliases",
        "registered",
        "status",
        "canonical_id",
        "merged_from",
        "metadata",
        "created_at",
        "updated_at",
    ]

    def _entity_select_clause(self, alias: str = "e") -> str:
        cols = ["id"] + self._ENTITY_NON_KEY_COLUMNS
        return ", ".join(f"{alias}.{c}" for c in cols)

    def _entity_columns(self) -> list[str]:
        return ["id"] + self._ENTITY_NON_KEY_COLUMNS

    def _row_to_entity(self, row: list[Any]) -> Entity:
        return Entity.from_db_row(list(row), self._entity_columns())

    def ensure_entity(self, entity_name: str, entity_type: str = "UNKNOWN") -> bool:
        """确保实体存在。

        如果命中的节点已 status=merged 且有 canonical_id：
        不创建新节点；返回 False 表示"未新建"。调用方若需要拿到
        canonical id，请配合 resolve_canonical_entity_id。
        """
        resp = self.conn.execute(
            "MATCH (e:Entity {id: $id}) RETURN e.status, e.canonical_id",
            {"id": entity_name},
        )
        if resp.has_next():
            row = resp.get_next()
            # 命中已合并节点：不新建，也不复活
            if (row[0] or "active") == "merged" and row[1]:
                return False
            return False

        # 生成裸名 embedding（保留既有行为）
        embedding = None
        if self.embedding_client:
            embedding = self.embedding_client.get_embedding(entity_name)

        ts = int(time.time())
        self.conn.execute(
            """
            CREATE (:Entity {
                id: $id,
                type: $type,
                embedding: $embedding,
                description: $description,
                description_embedding: $description_embedding,
                aliases: $aliases,
                registered: $registered,
                status: $status,
                canonical_id: $canonical_id,
                merged_from: $merged_from,
                metadata: $metadata,
                created_at: $created_at,
                updated_at: $updated_at
            })
            """,
            {
                "id": entity_name,
                "type": entity_type,
                "embedding": embedding,
                "description": "",
                "description_embedding": None,
                "aliases": safe_json_dumps([]),
                "registered": False,
                "status": "active",
                "canonical_id": None,
                "merged_from": safe_json_dumps([]),
                "metadata": safe_json_dumps({}),
                "created_at": ts,
                "updated_at": ts,
            },
        )
        return True

    def resolve_canonical_entity_id(self, entity_id: str) -> str:
        """若节点 status=merged 且 canonical_id 非空，返回 canonical；否则返回原 id。"""
        resp = self.conn.execute(
            "MATCH (e:Entity {id: $id}) RETURN e.status, e.canonical_id",
            {"id": entity_id},
        )
        if resp.has_next():
            row = resp.get_next()
            if (row[0] or "active") == "merged" and row[1]:
                return str(row[1])
        return entity_id

    def get_all_entities(self) -> list[str]:
        """获取所有实体名称（含 merged 节点；调用方按需过滤）"""
        resp = self.conn.execute("MATCH (e:Entity) RETURN e.id")
        entities = []
        while resp.has_next():
            entities.append(resp.get_next()[0])
        return entities

    def get_entity_embeddings(self, entities: list[str]) -> dict[str, list[float]]:
        """获取实体嵌入向量"""
        query = "MATCH (e:Entity) WHERE e.id IN $entities RETURN e.id, e.embedding"
        resp = self.conn.execute(query, {"entities": entities})

        embeddings = {}
        while resp.has_next():
            row = resp.get_next()
            entity_id = row[0]
            embedding = row[1]
            if embedding:
                embeddings[entity_id] = list(embedding)

        return embeddings

    # ------------------ Registered Entity 操作 ------------------

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        resp = self.conn.execute(
            f"MATCH (e:Entity {{id: $id}}) RETURN {self._entity_select_clause('e')}",
            {"id": entity_id},
        )
        if resp.has_next():
            return self._row_to_entity(resp.get_next())
        return None

    def get_registered_entity(self, entity_id: str) -> Optional[Entity]:
        entity = self.get_entity(entity_id)
        if entity is None or not entity.registered:
            return None
        return entity

    def list_registered_entities_with_embeddings(self) -> list[Entity]:
        resp = self.conn.execute(
            f"""
            MATCH (e:Entity)
            WHERE e.registered = true AND (e.status IS NULL OR e.status = 'active')
            RETURN {self._entity_select_clause('e')}
            """
        )
        out: list[Entity] = []
        while resp.has_next():
            out.append(self._row_to_entity(resp.get_next()))
        return out

    def register_entity_node(
        self,
        entity_id: str,
        entity_type: str,
        description: str,
        description_embedding: Optional[list[float]],
        aliases: list[str],
        metadata: dict[str, Any],
        created_at: int,
        name_embedding: Optional[list[float]] = None,
    ) -> dict[str, Any]:
        existing = self.get_entity(entity_id)
        ts = int(created_at or time.time())
        norm_aliases = list({a for a in (aliases or []) if a})
        existed_as_extracted = False
        mode = "created"

        if existing is None:
            # 创建：name_embedding 优先采用传入的；否则按裸名生成
            name_emb = name_embedding
            if name_emb is None and self.embedding_client:
                try:
                    name_emb = self.embedding_client.get_embedding(entity_id)
                except Exception:
                    name_emb = None
            self.conn.execute(
                """
                CREATE (:Entity {
                    id: $id,
                    type: $type,
                    embedding: $embedding,
                    description: $description,
                    description_embedding: $description_embedding,
                    aliases: $aliases,
                    registered: $registered,
                    status: $status,
                    canonical_id: $canonical_id,
                    merged_from: $merged_from,
                    metadata: $metadata,
                    created_at: $created_at,
                    updated_at: $updated_at
                })
                """,
                {
                    "id": entity_id,
                    "type": entity_type or "UNKNOWN",
                    "embedding": name_emb,
                    "description": description or "",
                    "description_embedding": description_embedding,
                    "aliases": safe_json_dumps(norm_aliases),
                    "registered": True,
                    "status": "active",
                    "canonical_id": None,
                    "merged_from": safe_json_dumps([]),
                    "metadata": safe_json_dumps(metadata or {}),
                    "created_at": ts,
                    "updated_at": ts,
                },
            )
            mode = "created"
        else:
            if not existing.registered:
                existed_as_extracted = True
                mode = "promoted"
            else:
                mode = "updated"
            # 原地更新关键字段
            self.conn.execute(
                """
                MATCH (e:Entity {id: $id})
                SET e.type = $type,
                    e.description = $description,
                    e.description_embedding = $description_embedding,
                    e.aliases = $aliases,
                    e.registered = true,
                    e.status = 'active',
                    e.metadata = $metadata,
                    e.updated_at = $updated_at
                """,
                {
                    "id": entity_id,
                    "type": entity_type or existing.type or "UNKNOWN",
                    "description": description or "",
                    "description_embedding": description_embedding,
                    "aliases": safe_json_dumps(
                        sorted(set(list(existing.aliases or []) + norm_aliases))
                    ),
                    "metadata": safe_json_dumps(metadata or existing.metadata or {}),
                    "updated_at": ts,
                },
            )
        return {"mode": mode, "existed_as_extracted": existed_as_extracted}

    def update_entity_attributes(
        self,
        entity_id: str,
        *,
        description: Optional[str] = None,
        description_embedding: Optional[list[float]] = None,
        entity_type: Optional[str] = None,
        add_aliases: Optional[list[str]] = None,
        remove_aliases: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
        updated_at: int = 0,
    ) -> None:
        existing = self.get_entity(entity_id)
        if existing is None:
            return
        ts = int(updated_at or time.time())

        new_aliases = list(existing.aliases or [])
        if add_aliases:
            for alias in add_aliases:
                if alias and alias not in new_aliases:
                    new_aliases.append(alias)
        if remove_aliases:
            remove_set = {a for a in remove_aliases if a}
            new_aliases = [a for a in new_aliases if a not in remove_set]

        new_description = (
            description if description is not None else existing.description
        )
        new_description_embedding = (
            description_embedding
            if description_embedding is not None
            else existing.description_embedding
        )
        new_type = entity_type or existing.type or "UNKNOWN"
        new_metadata = metadata if metadata is not None else existing.metadata or {}

        self.conn.execute(
            """
            MATCH (e:Entity {id: $id})
            SET e.type = $type,
                e.description = $description,
                e.description_embedding = $description_embedding,
                e.aliases = $aliases,
                e.metadata = $metadata,
                e.updated_at = $updated_at
            """,
            {
                "id": entity_id,
                "type": new_type,
                "description": new_description or "",
                "description_embedding": new_description_embedding,
                "aliases": safe_json_dumps(new_aliases),
                "metadata": safe_json_dumps(new_metadata),
                "updated_at": ts,
            },
        )

    def unregister_entity_node(self, entity_id: str) -> Optional[Entity]:
        """物理删除一个注册实体节点（DETACH DELETE），仅供注册回滚等极少场景使用。

        - 仅对 registered=True 的节点生效，避免误删抽取节点；
        - 删除前返回旧快照供调用方记录审计；
        - 同时切断所有 INVOLVES / HAS_PATTERN 等关系（DETACH 语义）。
        """
        existing = self.get_entity(entity_id)
        if existing is None or not existing.registered:
            return None
        self.conn.execute(
            """
            MATCH (e:Entity {id: $id})
            DETACH DELETE e
            """,
            {"id": entity_id},
        )
        return existing

    def add_entity_alias(self, canonical_id: str, alias: str) -> None:
        if not alias:
            return
        existing = self.get_entity(canonical_id)
        if existing is None or not existing.registered:
            return
        if alias == canonical_id or alias in (existing.aliases or []):
            return
        new_aliases = list(existing.aliases or []) + [alias]
        ts = int(time.time())
        self.conn.execute(
            "MATCH (e:Entity {id: $id}) SET e.aliases = $aliases, e.updated_at = $ts",
            {"id": canonical_id, "aliases": safe_json_dumps(new_aliases), "ts": ts},
        )

    def relink_entity_references(
        self,
        source_entity_id: str,
        target_entity_id: str,
        timestamp: int,
    ) -> int:
        """把 (:Event)-[:INVOLVES]->(:Entity {id: source}) 全部迁移到 target，并去重。"""
        if source_entity_id == target_entity_id:
            return 0
        ts = int(timestamp)
        moved = 0
        resp = self.conn.execute(
            """
            MATCH (e:Event)-[r:INVOLVES]->(:Entity {id: $source})
            RETURN e.id, r.t_created, r.t_expired, r.t_valid, r.t_invalid, r.c_valid
            """,
            {"source": source_entity_id},
        )
        rows = []
        while resp.has_next():
            rows.append(list(resp.get_next()))
        for row in rows:
            event_id = row[0]
            existing = self.get_involves_relation(event_id, target_entity_id)
            if existing:
                existing.t_created = min(existing.t_created, int(row[1] or ts))
                existing.t_valid = max(existing.t_valid, int(row[3] or ts))
                existing.c_valid = max(existing.c_valid, int(row[5] or 1))
                if row[2] is not None:
                    existing.t_expired = row[2]
                if row[4] is not None:
                    existing.t_invalid = row[4]
                self.update_involves_relation(existing)
            else:
                self.create_involves_relation(
                    event_id=event_id,
                    entity_id=target_entity_id,
                    t_created=int(row[1] or ts),
                    t_valid=int(row[3] or ts),
                    c_valid=int(row[5] or 1),
                    t_expired=row[2],
                    t_invalid=row[4],
                )
            moved += 1
        self.conn.execute(
            """
            MATCH (:Event)-[r:INVOLVES]->(:Entity {id: $source})
            DELETE r
            """,
            {"source": source_entity_id},
        )
        return moved

    def mark_entity_merged(
        self,
        merged_id: str,
        canonical_id: str,
        merged_at: int,
    ) -> None:
        ts = int(merged_at or time.time())
        self.conn.execute(
            """
            MATCH (e:Entity {id: $id})
            SET e.status = 'merged',
                e.canonical_id = $canonical_id,
                e.updated_at = $ts
            """,
            {"id": merged_id, "canonical_id": canonical_id, "ts": ts},
        )

    def save_entity_merge_trace(
        self,
        source_entity_id: str,
        target_entity_id: str,
        merge_reason: str,
        similarity_score: float,
        merged_at: int,
        strategy_version: str,
    ) -> None:
        self.conn.execute(
            """
            MATCH (s:Entity {id: $source}), (t:Entity {id: $target})
            CREATE (s)-[:ENTITY_MERGE_TRACE {
                merge_reason: $merge_reason,
                similarity_score: $similarity_score,
                merged_at: $merged_at,
                strategy_version: $strategy_version
            }]->(t)
            """,
            {
                "source": source_entity_id,
                "target": target_entity_id,
                "merge_reason": merge_reason or "",
                "similarity_score": float(similarity_score or 0.0),
                "merged_at": int(merged_at or time.time()),
                "strategy_version": strategy_version or "v1",
            },
        )

    # ==================== Entity Pattern 操作 ====================
    # v2 模型：每个 Entity 至多绑定 1 个 Pattern 节点（1:1）。CRUD 为 put / get / delete。
    # 字段：id, entity_id, content, status, created_at, updated_at。status 当前恒为 'active'。

    def _pattern_select_clause(self, alias: str = "p") -> str:
        return (
            f"{alias}.id, {alias}.entity_id, {alias}.content, {alias}.status, "
            f"{alias}.created_at, {alias}.updated_at"
        )

    def _row_to_pattern_dict(self, row: list[Any]) -> dict[str, Any]:
        return {
            "id": row[0],
            "entity_id": row[1] or "",
            "content": row[2] or "",
            "status": row[3] or "active",
            "created_at": int(row[4] or 0),
            "updated_at": int(row[5] or 0),
        }

    def put_entity_pattern(
        self,
        entity_id: str,
        content: str,
        *,
        now: Optional[int] = None,
    ) -> dict[str, Any]:
        """Upsert：实体已有 pattern 则覆盖 content；否则新建节点 + HAS_PATTERN 边。"""
        if self.get_registered_entity(entity_id) is None:
            raise ValueError(f"Registered entity not found: {entity_id}")
        ts = int(now or time.time())
        existing = self.get_entity_pattern(entity_id)
        if existing is not None:
            self.conn.execute(
                """
                MATCH (:Entity {id: $entity_id})-[:HAS_PATTERN]->(p:Pattern)
                SET p.content = $content, p.updated_at = $updated_at
                """,
                {"entity_id": entity_id, "content": content or "", "updated_at": ts},
            )
            return {"action": "updated", "pattern": self.get_entity_pattern(entity_id) or {}}

        pid = f"pattern_{uuid.uuid4().hex[:16]}"
        self.conn.execute(
            """
            CREATE (:Pattern {
                id: $id,
                entity_id: $entity_id,
                content: $content,
                status: 'active',
                created_at: $ts,
                updated_at: $ts
            })
            """,
            {"id": pid, "entity_id": entity_id, "content": content or "", "ts": ts},
        )
        self.conn.execute(
            """
            MATCH (e:Entity {id: $entity_id}), (p:Pattern {id: $pattern_id})
            CREATE (e)-[:HAS_PATTERN]->(p)
            """,
            {"entity_id": entity_id, "pattern_id": pid},
        )
        return {"action": "created", "pattern": self.get_entity_pattern(entity_id) or {}}

    def get_entity_pattern(self, entity_id: str) -> Optional[dict[str, Any]]:
        """读取实体绑定的 pattern。无则返回 None。"""
        resp = self.conn.execute(
            f"""
            MATCH (:Entity {{id: $entity_id}})-[:HAS_PATTERN]->(p:Pattern)
            RETURN {self._pattern_select_clause('p')}
            LIMIT 1
            """,
            {"entity_id": entity_id},
        )
        if resp.has_next():
            return self._row_to_pattern_dict(list(resp.get_next()))
        return None

    def delete_entity_pattern(self, entity_id: str) -> Optional[dict[str, Any]]:
        """硬删除实体的 pattern（含 HAS_PATTERN 边）。返回被删快照或 None。"""
        existing = self.get_entity_pattern(entity_id)
        if existing is None:
            return None
        self.conn.execute(
            """
            MATCH (:Entity {id: $entity_id})-[r:HAS_PATTERN]->(p:Pattern)
            DELETE r
            DETACH DELETE p
            """,
            {"entity_id": entity_id},
        )
        return existing

    # ==================== Relation 操作 ====================

    def get_involves_relation(
        self, event_id: str, entity_id: str
    ) -> Optional[EventRelation]:
        """获取INVOLVES关系"""
        resp = self.conn.execute(
            """
            MATCH (e:Event {id: $event_id})-[r:INVOLVES]->(en:Entity {id: $entity_id})
            RETURN r.t_created, r.t_expired, r.t_valid, r.t_invalid, r.c_valid
            """,
            {"event_id": event_id, "entity_id": entity_id},
        )
        if resp.has_next():
            row = resp.get_next()
            return EventRelation(
                event_id=event_id,
                entity_id=entity_id,
                t_created=row[0] or 0,
                t_expired=row[1],
                t_valid=row[2] or 0,
                t_invalid=row[3],
                c_valid=row[4] or 1,
            )
        return None

    def create_involves_relation(
        self,
        event_id: str,
        entity_id: str,
        t_created: int,
        t_valid: int,
        c_valid: int = 1,
        t_expired: Optional[int] = None,
        t_invalid: Optional[int] = None,
    ) -> None:
        """创建INVOLVES关系"""
        self.conn.execute(
            """
            MATCH (e:Event {id: $event_id}), (en:Entity {id: $entity_id})
            CREATE (e)-[:INVOLVES {
                t_created: $t_created,
                t_expired: $t_expired,
                t_valid: $t_valid,
                t_invalid: $t_invalid,
                c_valid: $c_valid
            }]->(en)
            """,
            {
                "event_id": event_id,
                "entity_id": entity_id,
                "t_created": t_created,
                "t_expired": t_expired,
                "t_valid": t_valid,
                "t_invalid": t_invalid,
                "c_valid": c_valid,
            },
        )

    def update_involves_relation(self, relation: EventRelation) -> None:
        """更新INVOLVES关系"""
        self.conn.execute(
            """
            MATCH (e:Event {id: $event_id})-[r:INVOLVES]->(en:Entity {id: $entity_id})
            SET r.t_valid = $t_valid,
                r.c_valid = $c_valid,
                r.t_expired = $t_expired,
                r.t_invalid = $t_invalid
            """,
            {
                "event_id": relation.event_id,
                "entity_id": relation.entity_id,
                "t_valid": relation.t_valid,
                "c_valid": relation.c_valid,
                "t_expired": relation.t_expired,
                "t_invalid": relation.t_invalid,
            },
        )

    def get_event_entities(self, event_id: str) -> list[str]:
        """获取事件关联的实体"""
        resp = self.conn.execute(
            """
            MATCH (e:Event {id: $event_id})-[:INVOLVES]->(en:Entity)
            RETURN en.id
            """,
            {"event_id": event_id},
        )
        entities = []
        while resp.has_next():
            entities.append(resp.get_next()[0])
        return entities

    def get_event_relations(self, event_id: str) -> list[EventRelation]:
        """获取事件的所有INVOLVES关系"""
        resp = self.conn.execute(
            """
            MATCH (e:Event {id: $event_id})-[r:INVOLVES]->(en:Entity)
            RETURN en.id, r.t_created, r.t_expired, r.t_valid, r.t_invalid, r.c_valid
            """,
            {"event_id": event_id},
        )
        relations = []
        while resp.has_next():
            row = resp.get_next()
            relations.append(EventRelation(
                event_id=event_id,
                entity_id=row[0],
                t_created=row[1] or 0,
                t_expired=row[2],
                t_valid=row[3] or 0,
                t_invalid=row[4],
                c_valid=row[5] or 1,
            ))
        return relations

    def link_event_to_episode(self, event_id: str, episode_id: str) -> None:
        """创建EXTRACTED_FROM关系"""
        self.conn.execute(
            """
            MATCH (e:Event {id: $event_id}), (ep:Episode {id: $episode_id})
            CREATE (e)-[:EXTRACTED_FROM]->(ep)
            """,
            {"event_id": event_id, "episode_id": episode_id},
        )

    def link_events_to_episode_batch(self, event_ids: list[str], episode_id: str) -> None:
        """批量创建 Event -> Episode 关系。"""
        if not event_ids:
            return
        self.conn.execute(
            """
            WITH $rows AS rows
            UNWIND rows AS row
            MATCH (e:Event {id: row.event_id}), (ep:Episode {id: row.episode_id})
            CREATE (e)-[:EXTRACTED_FROM]->(ep)
            """,
            {
                "rows": [
                    {"event_id": str(event_id), "episode_id": str(episode_id)}
                    for event_id in event_ids
                ]
            },
        )

    def promote_permanent_trait(
        self, user_id: str, event_id: str, t_created: int
    ) -> None:
        """提升为永久特征"""
        # 确保用户存在
        self._ensure_user(user_id)

        # 检查是否已存在
        resp = self.conn.execute(
            """
            MATCH (u:User {id: $user_id})-[r:PERMANENT_TRAIT]->(e:Event {id: $event_id})
            RETURN count(*)
            """,
            {"user_id": user_id, "event_id": event_id},
        )
        exists = resp.has_next() and resp.get_next()[0] > 0

        if not exists:
            self.conn.execute(
                """
                MATCH (u:User {id: $user_id}), (e:Event {id: $event_id})
                CREATE (u)-[:PERMANENT_TRAIT {t_created: $t_created}]->(e)
                """,
                {"user_id": user_id, "event_id": event_id, "t_created": t_created},
            )

    def upsert_event_relation(
        self,
        from_event_id: str,
        to_event_id: str,
        relation_type: str,
        description: str,
        confidence: float,
        evidence_span: str,
        source_episode_id: str,
        source_session_id: str,
        timestamp: int,
        operation: str = "",
        value_before: str = "",
        value_after: str = "",
        recall_channel: str = "",
        recall_score: float = 0.0,
    ) -> bool:
        relation_type = str(relation_type or "").strip()
        if not relation_type or not from_event_id or not to_event_id or from_event_id == to_event_id:
            return False
        params = {
            "from_event_id": from_event_id,
            "to_event_id": to_event_id,
            "relation_type": relation_type,
            "operation": str(operation or "").strip(),
            "description": str(description or "").strip(),
            "confidence": float(confidence),
            "evidence_span": str(evidence_span or "").strip(),
            "value_before": str(value_before or "").strip(),
            "value_after": str(value_after or "").strip(),
            "recall_channel": str(recall_channel or "").strip(),
            "recall_score": float(recall_score or 0.0),
            "source_episode_id": str(source_episode_id or "").strip(),
            "source_session_id": str(source_session_id or "").strip(),
            "timestamp": int(timestamp),
        }
        exists = self.conn.execute(
            """
            MATCH (:Event {id: $from_event_id})-[r:EVENT_RELATION {relation_type: $relation_type}]->(:Event {id: $to_event_id})
            RETURN count(*)
            """,
            {
                "from_event_id": from_event_id,
                "to_event_id": to_event_id,
                "relation_type": relation_type,
            },
        )
        found = exists.has_next() and exists.get_next()[0] > 0
        if found:
            self.conn.execute(
                """
                MATCH (:Event {id: $from_event_id})-[r:EVENT_RELATION {relation_type: $relation_type}]->(:Event {id: $to_event_id})
                SET r.operation = $operation,
                    r.description = $description,
                    r.confidence = $confidence,
                    r.evidence_span = $evidence_span,
                    r.value_before = $value_before,
                    r.value_after = $value_after,
                    r.recall_channel = $recall_channel,
                    r.recall_score = $recall_score,
                    r.source_episode_id = $source_episode_id,
                    r.source_session_id = $source_session_id,
                    r.updated_at = $timestamp
                """,
                params,
            )
            return False
        self.conn.execute(
            """
            MATCH (src:Event {id: $from_event_id}), (dst:Event {id: $to_event_id})
            CREATE (src)-[:EVENT_RELATION {
                relation_type: $relation_type,
                operation: $operation,
                description: $description,
                confidence: $confidence,
                evidence_span: $evidence_span,
                value_before: $value_before,
                value_after: $value_after,
                recall_channel: $recall_channel,
                recall_score: $recall_score,
                source_episode_id: $source_episode_id,
                source_session_id: $source_session_id,
                created_at: $timestamp,
                updated_at: $timestamp
            }]->(dst)
            """,
            params,
        )
        return True

    def list_event_event_edges(
        self,
        limit: int = 200,
        event_statuses: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": int(limit)}
        filters: list[str] = []
        filters = self._apply_status_filters("src", event_statuses, params, filters)
        if event_statuses:
            filters.append("dst.status IN $dst_statuses")
            params["dst_statuses"] = list(event_statuses)
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        resp = self.conn.execute(
            f"""
            MATCH (src:Event)-[r:EVENT_RELATION]->(dst:Event)
            {where_clause}
            RETURN src.id, dst.id, r.relation_type, r.operation, r.description, r.confidence,
                   r.evidence_span, r.value_before, r.value_after, r.recall_channel, r.recall_score,
                   r.source_episode_id, r.source_session_id, r.created_at, r.updated_at
            ORDER BY r.updated_at DESC
            LIMIT $limit
            """,
            params,
        )
        rows = []
        while resp.has_next():
            row = resp.get_next()
            rows.append(
                {
                    "from_event_id": row[0],
                    "to_event_id": row[1],
                    "relation_type": row[2] or "",
                    "operation": row[3] or "",
                    "description": row[4] or "",
                    "confidence": float(row[5] or 0.0),
                    "evidence_span": row[6] or "",
                    "value_before": row[7] or "",
                    "value_after": row[8] or "",
                    "recall_channel": row[9] or "",
                    "recall_score": float(row[10] or 0.0),
                    "source_episode_id": row[11] or "",
                    "source_session_id": row[12] or "",
                    "created_at": int(row[13] or 0),
                    "updated_at": int(row[14] or 0),
                }
            )
        return rows

    def _ensure_user(self, user_id: str) -> None:
        """确保用户存在"""
        resp = self.conn.execute(
            "MATCH (u:User {id: $id}) RETURN count(*)",
            {"id": user_id},
        )
        exists = resp.has_next() and resp.get_next()[0] > 0
        if not exists:
            self.conn.execute("CREATE (:User {id: $id})", {"id": user_id})

    # ==================== Dynamic Evolution 操作 ====================

    def save_context(self, context: Context) -> None:
        fields = context.to_db_fields()
        self.conn.execute(
            """
            CREATE (:Context {
                id: $id,
                context_type: $context_type,
                subtype: $subtype,
                summary: $summary,
                description: $description,
                confidence: $confidence,
                support_count: $support_count,
                created_at: $created_at,
                updated_at: $updated_at,
                valid_from: $valid_from,
                valid_to: $valid_to,
                last_seen_at: $last_seen_at,
                status: $status,
                source_refs: $source_refs,
                merged_from: $merged_from,
                embedding: $embedding
            })
            """,
            fields,
        )

    def get_context(self, context_id: str) -> Optional[Context]:
        resp = self.conn.execute(
            """
            MATCH (c:Context {id: $id})
            RETURN c.id, c.context_type, c.subtype, c.summary, c.description,
                   c.confidence, c.support_count, c.created_at, c.updated_at,
                   c.valid_from, c.valid_to, c.last_seen_at, c.status,
                   c.source_refs, c.merged_from, c.embedding
            """,
            {"id": context_id},
        )
        if not resp.has_next():
            return None
        row = resp.get_next()
        cols = [
            "id", "context_type", "subtype", "summary", "description",
            "confidence", "support_count", "created_at", "updated_at",
            "valid_from", "valid_to", "last_seen_at", "status",
            "source_refs", "merged_from", "embedding",
        ]
        return Context.from_db_row(list(row), cols)

    def update_context(self, context: Context) -> None:
        fields = context.to_db_fields()
        self.conn.execute(
            """
            MATCH (c:Context {id: $id})
            SET c.context_type = $context_type,
                c.subtype = $subtype,
                c.summary = $summary,
                c.description = $description,
                c.confidence = $confidence,
                c.support_count = $support_count,
                c.updated_at = $updated_at,
                c.valid_from = $valid_from,
                c.valid_to = $valid_to,
                c.last_seen_at = $last_seen_at,
                c.status = $status,
                c.source_refs = $source_refs,
                c.merged_from = $merged_from,
                c.embedding = $embedding
            """,
            {
                "id": fields["id"],
                "context_type": fields["context_type"],
                "subtype": fields["subtype"],
                "summary": fields["summary"],
                "description": fields["description"],
                "confidence": fields["confidence"],
                "support_count": fields["support_count"],
                "updated_at": fields["updated_at"],
                "valid_from": fields["valid_from"],
                "valid_to": fields["valid_to"],
                "last_seen_at": fields["last_seen_at"],
                "status": fields["status"],
                "source_refs": fields["source_refs"],
                "merged_from": fields["merged_from"],
                "embedding": fields["embedding"],
            },
        )

    def find_context_candidates(
        self,
        context_type: str,
        subtype: str = "",
        limit: int = 20,
        only_active: bool = True,
    ) -> list[Context]:
        filters = ["c.context_type = $context_type"]
        params: dict[str, Any] = {"context_type": context_type, "limit": int(limit)}
        if subtype:
            filters.append("c.subtype = $subtype")
            params["subtype"] = subtype
        if only_active:
            filters.append("c.status = 'active'")
        where_clause = " AND ".join(filters)
        resp = self.conn.execute(
            f"""
            MATCH (c:Context)
            WHERE {where_clause}
            RETURN c.id, c.context_type, c.subtype, c.summary, c.description,
                   c.confidence, c.support_count, c.created_at, c.updated_at,
                   c.valid_from, c.valid_to, c.last_seen_at, c.status,
                   c.source_refs, c.merged_from, c.embedding
            ORDER BY c.last_seen_at DESC
            LIMIT $limit
            """,
            params,
        )
        cols = [
            "id", "context_type", "subtype", "summary", "description",
            "confidence", "support_count", "created_at", "updated_at",
            "valid_from", "valid_to", "last_seen_at", "status",
            "source_refs", "merged_from", "embedding",
        ]
        result = []
        while resp.has_next():
            result.append(Context.from_db_row(list(resp.get_next()), cols))
        return result

    def find_contexts_summary_index(
        self,
        context_type: str,
        only_active: bool = True,
    ) -> list[tuple[str, str]]:
        filters = ["c.context_type = $context_type"]
        params: dict[str, Any] = {"context_type": context_type}
        if only_active:
            filters.append("c.status = 'active'")
        where_clause = " AND ".join(filters)
        resp = self.conn.execute(
            f"""
            MATCH (c:Context)
            WHERE {where_clause}
            RETURN c.id, c.summary
            """,
            params,
        )
        result: list[tuple[str, str]] = []
        while resp.has_next():
            row = resp.get_next()
            result.append((str(row[0]), str(row[1] or "")))
        return result

    def link_event_to_context(
        self,
        event_id: str,
        context_id: str,
        confidence: float,
        weight: float,
        original_signal: str,
        evidence_span: str,
        timestamp: int,
    ) -> None:
        exists = self.conn.execute(
            """
            MATCH (:Event {id: $event_id})-[r:IN_REL]->(:Context {id: $context_id})
            RETURN count(*)
            """,
            {"event_id": event_id, "context_id": context_id},
        )
        found = exists.has_next() and exists.get_next()[0] > 0
        if found:
            self.conn.execute(
                """
                MATCH (:Event {id: $event_id})-[r:IN_REL]->(:Context {id: $context_id})
                SET r.confidence = $confidence,
                    r.weight = $weight,
                    r.updated_at = $timestamp,
                    r.last_seen_at = $timestamp,
                    r.original_signal = $original_signal,
                    r.evidence_span = $evidence_span
                """,
                {
                    "event_id": event_id,
                    "context_id": context_id,
                    "confidence": float(confidence),
                    "weight": float(weight),
                    "timestamp": int(timestamp),
                    "original_signal": original_signal,
                    "evidence_span": evidence_span,
                },
            )
            return

        self.conn.execute(
            """
            MATCH (e:Event {id: $event_id}), (c:Context {id: $context_id})
            CREATE (e)-[:IN_REL {
                confidence: $confidence,
                weight: $weight,
                original_signal: $original_signal,
                evidence_span: $evidence_span,
                created_at: $timestamp,
                updated_at: $timestamp,
                last_seen_at: $timestamp
            }]->(c)
            """,
            {
                "event_id": event_id,
                "context_id": context_id,
                "confidence": float(confidence),
                "weight": float(weight),
                "timestamp": int(timestamp),
                "original_signal": original_signal,
                "evidence_span": evidence_span,
            },
        )

    def get_recent_events(
        self,
        current_time: int,
        window_seconds: int,
        limit: int = 100,
    ) -> list[Event]:
        min_ts = int(current_time) - int(window_seconds)
        resp = self.conn.execute(
            f"""
            MATCH (e:Event)
            WHERE e.last_active >= $min_ts
            RETURN {self._event_select_clause('e')}
            ORDER BY e.last_active DESC
            LIMIT $limit
            """,
            {"min_ts": min_ts, "limit": int(limit)},
        )
        events = []
        while resp.has_next():
            events.append(self._row_to_event(list(resp.get_next())))
        return events

    def get_active_events_with_embeddings(self, limit: int = 200) -> list[Event]:
        resp = self.conn.execute(
            f"""
            MATCH (e:Event)
            WHERE e.status = 'active'
            RETURN {self._event_select_clause('e')}
            ORDER BY e.last_active DESC
            LIMIT $limit
            """,
            {"limit": int(max(limit, 1))},
        )
        events: list[Event] = []
        while resp.has_next():
            event = self._row_to_event(list(resp.get_next()))
            if event.embedding:
                events.append(event)
        return events

    def find_events_by_state_key(
        self,
        entity: str,
        attribute: str,
        limit: int = 20,
    ) -> list[Event]:
        entity_text = str(entity or "").strip()
        attribute_text = str(attribute or "").strip()
        if not entity_text or not attribute_text:
            return []
        resp = self.conn.execute(
            f"""
            MATCH (e:Event)
            WHERE e.status = 'active'
              AND e.payload CONTAINS $entity_text
              AND e.payload CONTAINS $attribute_text
            RETURN {self._event_select_clause('e')}
            ORDER BY e.last_active DESC
            LIMIT $limit
            """,
            {
                "entity_text": entity_text,
                "attribute_text": attribute_text,
                "limit": int(max(limit, 1)),
            },
        )
        events: list[Event] = []
        while resp.has_next():
            events.append(self._row_to_event(list(resp.get_next())))
        return events

    def find_events_by_thread(self, thread_id: str, limit: int = 20) -> list[Event]:
        thread_text = str(thread_id or "").strip()
        if not thread_text:
            return []
        resp = self.conn.execute(
            f"""
            MATCH (e:Event)
            WHERE e.status = 'active'
              AND e.payload CONTAINS $thread_text
            RETURN {self._event_select_clause('e')}
            ORDER BY e.last_active DESC
            LIMIT $limit
            """,
            {
                "thread_text": thread_text,
                "limit": int(max(limit, 1)),
            },
        )
        events: list[Event] = []
        while resp.has_next():
            events.append(self._row_to_event(list(resp.get_next())))
        return events

    def get_event_contexts(self, event_id: str) -> list[Context]:
        resp = self.conn.execute(
            """
            MATCH (:Event {id: $event_id})-[r:IN_REL]->(c:Context)
            RETURN c.id, c.context_type, c.subtype, c.summary, c.description,
                   c.confidence, c.support_count, c.created_at, c.updated_at,
                   c.valid_from, c.valid_to, c.last_seen_at, c.status,
                   c.source_refs, c.merged_from, c.embedding
            ORDER BY r.weight DESC, r.confidence DESC
            """,
            {"event_id": event_id},
        )
        cols = [
            "id", "context_type", "subtype", "summary", "description",
            "confidence", "support_count", "created_at", "updated_at",
            "valid_from", "valid_to", "last_seen_at", "status",
            "source_refs", "merged_from", "embedding",
        ]
        result = []
        while resp.has_next():
            result.append(Context.from_db_row(list(resp.get_next()), cols))
        return result

    def retrieve_candidate_contexts_for_query(
        self,
        query: str,
        query_entities: list[str],
        limit: int = 20,
    ) -> list[Context]:
        resp = self.conn.execute(
            """
            MATCH (c:Context)
            WHERE c.status = 'active'
            RETURN c.id, c.context_type, c.subtype, c.summary, c.description,
                   c.confidence, c.support_count, c.created_at, c.updated_at,
                   c.valid_from, c.valid_to, c.last_seen_at, c.status,
                   c.source_refs, c.merged_from, c.embedding
            ORDER BY c.last_seen_at DESC
            LIMIT $limit
            """,
            {"limit": int(max(limit * 4, limit))},
        )
        cols = [
            "id", "context_type", "subtype", "summary", "description",
            "confidence", "support_count", "created_at", "updated_at",
            "valid_from", "valid_to", "last_seen_at", "status",
            "source_refs", "merged_from", "embedding",
        ]
        scored: list[tuple[float, Context]] = []
        fallback_recent: list[Context] = []
        while resp.has_next():
            context = Context.from_db_row(list(resp.get_next()), cols)
            haystack = f"{context.summary} {context.description}".lower()
            score = self._text_similarity_score(haystack, query, query_entities)
            if score > 0.0 or not (query or query_entities):
                score += 0.15 * context.confidence
                scored.append((score, context))
                continue
            # Semantic miss fallback: keep a small recent-confidence pool to prevent sparse zero-recall.
            fallback_recent.append(context)
        scored.sort(key=lambda item: item[0], reverse=True)
        result = [context for _, context in scored[:limit]]
        if len(result) >= limit or not fallback_recent:
            return result

        fallback_recent.sort(
            key=lambda c: (
                c.last_seen_at or c.updated_at or c.valid_from or c.created_at,
                c.confidence,
                c.support_count,
            ),
            reverse=True,
        )
        fallback_budget = min(max(2, limit // 4), limit - len(result))
        existing_ids = {c.id for c in result}
        for context in fallback_recent:
            if context.id in existing_ids:
                continue
            result.append(context)
            existing_ids.add(context.id)
            if len(result) >= limit or fallback_budget <= 1:
                break
            fallback_budget -= 1
        return result

    def _apply_status_filters(
        self,
        alias: str,
        statuses: Optional[list[str]],
        params: dict[str, Any],
        filters: Optional[list[str]] = None,
    ) -> list[str]:
        where_filters = list(filters or [])
        if statuses:
            where_filters.append(f"{alias}.status IN $statuses")
            params["statuses"] = list(statuses)
        return where_filters

    def retrieve_events_by_contexts(
        self,
        context_ids: list[str],
        limit: int = 50,
    ) -> list[Event]:
        if not context_ids:
            return []
        resp = self.conn.execute(
            f"""
            MATCH (e:Event)-[r:IN_REL]->(c:Context)
            WHERE c.id IN $context_ids
            RETURN DISTINCT {self._event_select_clause('e')}
            ORDER BY e.last_active DESC
            LIMIT $limit
            """,
            {"context_ids": context_ids, "limit": int(limit)},
        )
        result = []
        while resp.has_next():
            result.append(self._row_to_event(list(resp.get_next())))
        return result

    def save_event_merge_trace(
        self,
        source_event_id: str,
        target_event_id: str,
        merge_reason: str,
        similarity_score: float,
        merged_at: int,
        strategy_version: str,
    ) -> None:
        self.conn.execute(
            """
            MATCH (src:Event {id: $source_event_id}), (dst:Event {id: $target_event_id})
            CREATE (src)-[:EVENT_MERGE_TRACE {
                merge_reason: $merge_reason,
                similarity_score: $similarity_score,
                merged_at: $merged_at,
                strategy_version: $strategy_version
            }]->(dst)
            """,
            {
                "source_event_id": source_event_id,
                "target_event_id": target_event_id,
                "merge_reason": merge_reason,
                "similarity_score": float(similarity_score),
                "merged_at": int(merged_at),
                "strategy_version": strategy_version,
            },
        )

    def list_event_merge_traces(self, event_id: str) -> list[dict[str, Any]]:
        resp = self.conn.execute(
            """
            MATCH (src:Event)-[r:EVENT_MERGE_TRACE]->(dst:Event)
            WHERE src.id = $event_id OR dst.id = $event_id
            RETURN src.id, dst.id, r.merge_reason, r.similarity_score, r.merged_at, r.strategy_version
            ORDER BY r.merged_at DESC
            """,
            {"event_id": event_id},
        )
        rows: list[dict[str, Any]] = []
        while resp.has_next():
            row = resp.get_next()
            rows.append(
                {
                    "source_event_id": row[0],
                    "target_event_id": row[1],
                    "merge_reason": row[2] or "",
                    "similarity_score": float(row[3] or 0.0),
                    "merged_at": int(row[4] or 0),
                    "strategy_version": row[5] or "",
                }
            )
        return rows

    def archive_event(self, event_id: str, archived_at: int) -> None:
        self.conn.execute(
            """
            MATCH (e:Event {id: $id})
            SET e.status = 'archived',
                e.updated_at = $archived_at,
                e.valid_to = CASE WHEN e.valid_to IS NULL THEN $archived_at ELSE e.valid_to END
            """,
            {"id": event_id, "archived_at": int(archived_at)},
        )

    def archive_context(self, context_id: str, archived_at: int) -> None:
        self.conn.execute(
            """
            MATCH (c:Context {id: $id})
            SET c.status = 'deprecated',
                c.updated_at = $archived_at,
                c.valid_to = CASE WHEN c.valid_to IS NULL THEN $archived_at ELSE c.valid_to END
            """,
            {"id": context_id, "archived_at": int(archived_at)},
        )

    def delete_event(self, event_id: str) -> None:
        self.conn.execute(
            """
            MATCH (e:Event {id: $id})
            DETACH DELETE e
            """,
            {"id": event_id},
        )

    def delete_context(self, context_id: str) -> None:
        self.conn.execute(
            """
            MATCH (c:Context {id: $id})
            DETACH DELETE c
            """,
            {"id": context_id},
        )

    def relink_event_references(
        self,
        source_event_id: str,
        target_event_id: str,
        timestamp: int,
    ) -> dict[str, int]:
        if source_event_id == target_event_id:
            return {}

        ts = int(timestamp)
        moved = {
            "entity_relations": 0,
            "episode_links": 0,
            "context_links": 0,
            "permanent_traits": 0,
            "event_relations": 0,
        }

        resp = self.conn.execute(
            """
            MATCH (:Event {id: $source_event_id})-[r:INVOLVES]->(en:Entity)
            RETURN en.id, r.t_created, r.t_expired, r.t_valid, r.t_invalid, r.c_valid
            """,
            {"source_event_id": source_event_id},
        )
        while resp.has_next():
            row = resp.get_next()
            entity_id = row[0]
            existing = self.get_involves_relation(target_event_id, entity_id)
            if existing:
                existing.t_created = min(existing.t_created, int(row[1] or ts))
                existing.t_valid = max(existing.t_valid, int(row[3] or ts))
                existing.c_valid = max(existing.c_valid, int(row[5] or 1))
                if row[2] is not None:
                    existing.t_expired = row[2]
                if row[4] is not None:
                    existing.t_invalid = row[4]
                self.update_involves_relation(existing)
            else:
                self.create_involves_relation(
                    event_id=target_event_id,
                    entity_id=entity_id,
                    t_created=int(row[1] or ts),
                    t_valid=int(row[3] or ts),
                    c_valid=int(row[5] or 1),
                    t_expired=row[2],
                    t_invalid=row[4],
                )
            moved["entity_relations"] += 1
        self.conn.execute(
            """
            MATCH (:Event {id: $source_event_id})-[r:INVOLVES]->(:Entity)
            DELETE r
            """,
            {"source_event_id": source_event_id},
        )

        resp = self.conn.execute(
            """
            MATCH (:Event {id: $source_event_id})-[:EXTRACTED_FROM]->(ep:Episode)
            RETURN ep.id
            """,
            {"source_event_id": source_event_id},
        )
        while resp.has_next():
            episode_id = resp.get_next()[0]
            exists = self.conn.execute(
                """
                MATCH (:Event {id: $target_event_id})-[:EXTRACTED_FROM]->(:Episode {id: $episode_id})
                RETURN count(*)
                """,
                {"target_event_id": target_event_id, "episode_id": episode_id},
            )
            if not (exists.has_next() and exists.get_next()[0] > 0):
                self.link_event_to_episode(target_event_id, episode_id)
            moved["episode_links"] += 1
        self.conn.execute(
            """
            MATCH (:Event {id: $source_event_id})-[r:EXTRACTED_FROM]->(:Episode)
            DELETE r
            """,
            {"source_event_id": source_event_id},
        )

        resp = self.conn.execute(
            """
            MATCH (:Event {id: $source_event_id})-[r:IN_REL]->(c:Context)
            RETURN c.id, r.confidence, r.weight, r.original_signal, r.evidence_span, r.last_seen_at
            """,
            {"source_event_id": source_event_id},
        )
        while resp.has_next():
            row = resp.get_next()
            self.link_event_to_context(
                event_id=target_event_id,
                context_id=row[0],
                confidence=float(row[1] or 0.7),
                weight=float(row[2] or 1.0),
                original_signal=row[3] or "manual_merge",
                evidence_span=row[4] or "",
                timestamp=int(row[5] or ts),
            )
            moved["context_links"] += 1
        self.conn.execute(
            """
            MATCH (:Event {id: $source_event_id})-[r:IN_REL]->(:Context)
            DELETE r
            """,
            {"source_event_id": source_event_id},
        )

        resp = self.conn.execute(
            """
            MATCH (u:User)-[r:PERMANENT_TRAIT]->(:Event {id: $source_event_id})
            RETURN u.id, r.t_created
            """,
            {"source_event_id": source_event_id},
        )
        while resp.has_next():
            row = resp.get_next()
            self.promote_permanent_trait(row[0], target_event_id, int(row[1] or ts))
            moved["permanent_traits"] += 1
        self.conn.execute(
            """
            MATCH (:User)-[r:PERMANENT_TRAIT]->(:Event {id: $source_event_id})
            DELETE r
            """,
            {"source_event_id": source_event_id},
        )

        outgoing = self.conn.execute(
            """
            MATCH (:Event {id: $source_event_id})-[r:EVENT_RELATION]->(dst:Event)
            RETURN dst.id, r.relation_type, r.operation, r.description, r.confidence, r.evidence_span,
                   r.value_before, r.value_after, r.recall_channel, r.recall_score,
                   r.source_episode_id, r.source_session_id, r.updated_at
            """,
            {"source_event_id": source_event_id},
        )
        while outgoing.has_next():
            row = outgoing.get_next()
            dst_id = row[0]
            if dst_id == target_event_id:
                continue
            self.upsert_event_relation(
                from_event_id=target_event_id,
                to_event_id=dst_id,
                relation_type=row[1] or "关联",
                operation=row[2] or "",
                description=row[3] or "",
                confidence=float(row[4] or 0.0),
                evidence_span=row[5] or "",
                value_before=row[6] or "",
                value_after=row[7] or "",
                recall_channel=row[8] or "",
                recall_score=float(row[9] or 0.0),
                source_episode_id=row[10] or "",
                source_session_id=row[11] or "",
                timestamp=int(row[12] or ts),
            )
            moved["event_relations"] += 1
        self.conn.execute(
            """
            MATCH (:Event {id: $source_event_id})-[r:EVENT_RELATION]->(:Event)
            DELETE r
            """,
            {"source_event_id": source_event_id},
        )

        incoming = self.conn.execute(
            """
            MATCH (src:Event)-[r:EVENT_RELATION]->(:Event {id: $source_event_id})
            RETURN src.id, r.relation_type, r.operation, r.description, r.confidence, r.evidence_span,
                   r.value_before, r.value_after, r.recall_channel, r.recall_score,
                   r.source_episode_id, r.source_session_id, r.updated_at
            """,
            {"source_event_id": source_event_id},
        )
        while incoming.has_next():
            row = incoming.get_next()
            src_id = row[0]
            if src_id == target_event_id:
                continue
            self.upsert_event_relation(
                from_event_id=src_id,
                to_event_id=target_event_id,
                relation_type=row[1] or "关联",
                operation=row[2] or "",
                description=row[3] or "",
                confidence=float(row[4] or 0.0),
                evidence_span=row[5] or "",
                value_before=row[6] or "",
                value_after=row[7] or "",
                recall_channel=row[8] or "",
                recall_score=float(row[9] or 0.0),
                source_episode_id=row[10] or "",
                source_session_id=row[11] or "",
                timestamp=int(row[12] or ts),
            )
            moved["event_relations"] += 1
        self.conn.execute(
            """
            MATCH (:Event)-[r:EVENT_RELATION]->(:Event {id: $source_event_id})
            DELETE r
            """,
            {"source_event_id": source_event_id},
        )
        return moved

    def relink_context_edges(
        self,
        source_context_id: str,
        target_context_id: str,
        timestamp: int,
    ) -> int:
        if source_context_id == target_context_id:
            return 0
        ts = int(timestamp)
        moved = 0
        resp = self.conn.execute(
            """
            MATCH (e:Event)-[r:IN_REL]->(:Context {id: $source_context_id})
            RETURN e.id, r.confidence, r.weight, r.original_signal, r.evidence_span, r.last_seen_at
            """,
            {"source_context_id": source_context_id},
        )
        while resp.has_next():
            row = resp.get_next()
            self.link_event_to_context(
                event_id=row[0],
                context_id=target_context_id,
                confidence=float(row[1] or 0.7),
                weight=float(row[2] or 1.0),
                original_signal=row[3] or "manual_context_merge",
                evidence_span=row[4] or "",
                timestamp=int(row[5] or ts),
            )
            moved += 1
        self.conn.execute(
            """
            MATCH (:Event)-[r:IN_REL]->(:Context {id: $source_context_id})
            DELETE r
            """,
            {"source_context_id": source_context_id},
        )
        return moved

    def list_events(
        self,
        limit: int = 50,
        query: str = "",
        statuses: Optional[list[str]] = None,
    ) -> list[Event]:
        params: dict[str, Any] = {"limit": int(max(limit, 1) * (6 if query else 1))}
        filters = self._apply_status_filters("e", statuses, params)
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        resp = self.conn.execute(
            f"""
            MATCH (e:Event)
            {where_clause}
            RETURN {self._event_select_clause('e')}
            ORDER BY e.last_active DESC
            LIMIT $limit
            """,
            params,
        )
        events: list[tuple[float, Event]] = []
        while resp.has_next():
            event = self._row_to_event(list(resp.get_next()))
            score = 1.0
            if query:
                haystack = " ".join(
                    [
                        event.summary,
                        event.action,
                        event.causality,
                        safe_json_dumps(event.payload),
                    ]
                )
                score = self._text_similarity_score(haystack, query, [])
                if score <= 0.0:
                    continue
            events.append((score, event))
        events.sort(key=lambda item: (item[0], item[1].last_active or item[1].timestamp), reverse=True)
        return [event for _, event in events[:limit]]

    def list_contexts(
        self,
        limit: int = 50,
        query: str = "",
        statuses: Optional[list[str]] = None,
    ) -> list[Context]:
        params: dict[str, Any] = {"limit": int(max(limit, 1) * (6 if query else 1))}
        filters = self._apply_status_filters("c", statuses, params)
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        resp = self.conn.execute(
            f"""
            MATCH (c:Context)
            {where_clause}
            RETURN c.id, c.context_type, c.subtype, c.summary, c.description,
                   c.confidence, c.support_count, c.created_at, c.updated_at,
                   c.valid_from, c.valid_to, c.last_seen_at, c.status,
                   c.source_refs, c.merged_from, c.embedding
            ORDER BY c.last_seen_at DESC
            LIMIT $limit
            """,
            params,
        )
        cols = [
            "id", "context_type", "subtype", "summary", "description",
            "confidence", "support_count", "created_at", "updated_at",
            "valid_from", "valid_to", "last_seen_at", "status",
            "source_refs", "merged_from", "embedding",
        ]
        rows: list[tuple[float, Context]] = []
        while resp.has_next():
            context = Context.from_db_row(list(resp.get_next()), cols)
            score = 1.0
            if query:
                score = self._text_similarity_score(
                    f"{context.summary} {context.description}",
                    query,
                    [],
                )
                if score <= 0.0:
                    continue
            rows.append((score, context))
        rows.sort(key=lambda item: (item[0], item[1].last_seen_at), reverse=True)
        return [context for _, context in rows[:limit]]

    def list_event_context_edges(
        self,
        limit: int = 200,
        event_statuses: Optional[list[str]] = None,
        context_statuses: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": int(limit)}
        filters: list[str] = []
        filters = self._apply_status_filters("e", event_statuses, params, filters)
        if context_statuses:
            filters.append("c.status IN $context_statuses")
            params["context_statuses"] = list(context_statuses)
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        resp = self.conn.execute(
            f"""
            MATCH (e:Event)-[r:IN_REL]->(c:Context)
            {where_clause}
            RETURN e.id, c.id, r.confidence, r.weight, r.original_signal, r.evidence_span, r.last_seen_at
            ORDER BY r.last_seen_at DESC
            LIMIT $limit
            """,
            params,
        )
        rows = []
        while resp.has_next():
            row = resp.get_next()
            rows.append(
                {
                    "event_id": row[0],
                    "context_id": row[1],
                    "confidence": float(row[2] or 0.0),
                    "weight": float(row[3] or 0.0),
                    "original_signal": row[4] or "",
                    "evidence_span": row[5] or "",
                    "last_seen_at": int(row[6] or 0),
                }
            )
        return rows

    # ==================== 统计 ====================

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息"""
        stats = {}

        # 统计 Episodes
        resp = self.conn.execute("MATCH (e:Episode) RETURN count(*)")
        stats["episode_count"] = resp.get_next()[0] if resp.has_next() else 0

        # 统计 Events
        resp = self.conn.execute("MATCH (e:Event) RETURN count(*)")
        stats["event_count"] = resp.get_next()[0] if resp.has_next() else 0

        # 统计 Entities
        resp = self.conn.execute("MATCH (e:Entity) RETURN count(*)")
        stats["entity_count"] = resp.get_next()[0] if resp.has_next() else 0

        # 统计 Context
        resp = self.conn.execute("MATCH (c:Context) RETURN count(*)")
        stats["context_count"] = resp.get_next()[0] if resp.has_next() else 0

        resp = self.conn.execute("MATCH (p:Pattern) RETURN count(*)")
        stats["pattern_count"] = resp.get_next()[0] if resp.has_next() else 0

        # 统计 INVOLVES 关系
        resp = self.conn.execute("MATCH ()-[r:INVOLVES]->() RETURN count(r)")
        stats["involves_count"] = resp.get_next()[0] if resp.has_next() else 0

        resp = self.conn.execute("MATCH ()-[r:IN_REL]->() RETURN count(r)")
        stats["in_count"] = resp.get_next()[0] if resp.has_next() else 0
        stats["abstract_to_count"] = stats["in_count"]

        resp = self.conn.execute("MATCH ()-[r:EVENT_MERGE_TRACE]->() RETURN count(r)")
        stats["event_merge_trace_count"] = resp.get_next()[0] if resp.has_next() else 0

        resp = self.conn.execute("MATCH ()-[r:EVENT_RELATION]->() RETURN count(r)")
        stats["event_relation_count"] = resp.get_next()[0] if resp.has_next() else 0

        resp = self.conn.execute("MATCH ()-[r:HAS_PATTERN]->() RETURN count(r)")
        stats["has_pattern_count"] = resp.get_next()[0] if resp.has_next() else 0

        return stats
