# -*- coding: utf-8 -*-
"""KuzuStore - Kuzu 图数据库实现

实现 GraphStore 接口的 Kuzu 具体实现。
"""

import os
import re
from typing import Any, Optional

import kuzu

from ..core.episode import Episode
from ..core.event import Event, EventRelation
from ..core.context import Context
from ..core.pattern import Pattern
from ..core.entity import Entity
from ..utils import safe_json_dumps, safe_json_loads
from .graph_store import GraphStore


class KuzuStore(GraphStore):
    """Kuzu 图数据库实现

    职责：实现 GraphStore 接口，提供 Kuzu 数据库的具体操作。
    """

    def __init__(self, db_path: str, embedding_client=None):
        """初始化 Kuzu 存储

        Args:
            db_path: 数据库文件路径
            embedding_client: 嵌入向量客户端（用于实体嵌入生成）
        """
        self.db_path = self._normalize_db_path(db_path)
        self.embedding_client = embedding_client

        # 打开连接
        print(f"📁 Using Kuzu DB file: {self.db_path}")
        self.db = kuzu.Database(self.db_path)
        self.conn = kuzu.Connection(self.db)

        # 初始化 Schema
        self._init_schema()

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
        # 节点表
        self.conn.execute("""
            CREATE NODE TABLE IF NOT EXISTS Episode(
                id STRING,
                content STRING,
                timestamp INT64,
                PRIMARY KEY(id)
            )
        """)

        self.conn.execute("""
            CREATE NODE TABLE IF NOT EXISTS Event(
                id STRING,
                event_type STRING,
                summary STRING,
                participants STRING,
                time_range STRING,
                location STRING,
                action STRING,
                causality STRING,
                payload STRING,
                evidence STRING,
                consistency STRING,
                timestamp INT64,
                last_active INT64,
                created_at INT64,
                updated_at INT64,
                valid_from INT64,
                valid_to INT64,
                salience DOUBLE,
                confidence DOUBLE,
                source STRING,
                status STRING,
                support_count INT64,
                embedding FLOAT[1536],
                PRIMARY KEY(id)
            )
        """)

        self.conn.execute("""
            CREATE NODE TABLE IF NOT EXISTS Entity(
                id STRING,
                type STRING,
                embedding FLOAT[1536],
                PRIMARY KEY(id)
            )
        """)

        self.conn.execute("""
            CREATE NODE TABLE IF NOT EXISTS User(
                id STRING,
                PRIMARY KEY(id)
            )
        """)
        self.conn.execute("""
            CREATE NODE TABLE IF NOT EXISTS Context(
                id STRING,
                context_type STRING,
                subtype STRING,
                summary STRING,
                structured_slots STRING,
                confidence DOUBLE,
                support_count INT64,
                created_at INT64,
                updated_at INT64,
                valid_from INT64,
                valid_to INT64,
                last_seen_at INT64,
                status STRING,
                embedding FLOAT[1536],
                PRIMARY KEY(id)
            )
        """)
        self.conn.execute("""
            CREATE NODE TABLE IF NOT EXISTS Pattern(
                id STRING,
                pattern_type STRING,
                summary STRING,
                prototype_features STRING,
                support_count INT64,
                confidence DOUBLE,
                stability_score DOUBLE,
                drift_score DOUBLE,
                created_at INT64,
                updated_at INT64,
                valid_from INT64,
                valid_to INT64,
                last_seen_at INT64,
                status STRING,
                embedding FLOAT[1536],
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
                original_type STRING,
                created_at INT64,
                updated_at INT64,
                last_seen_at INT64
            )
        """)
        self.conn.execute("""
            CREATE REL TABLE IF NOT EXISTS NEXT(
                FROM Event TO Event,
                confidence DOUBLE,
                score DOUBLE,
                relation_hint STRING,
                created_at INT64,
                updated_at INT64,
                last_seen_at INT64,
                support_count INT64
            )
        """)
        self.conn.execute("""
            CREATE REL TABLE IF NOT EXISTS ABSTRACT_TO(
                FROM Event TO Pattern,
                confidence DOUBLE,
                contribution_weight DOUBLE,
                created_at INT64,
                updated_at INT64,
                last_reinforced_at INT64
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

        # Best-effort 迁移
        self._run_migrations()

    def _run_migrations(self) -> None:
        """运行数据库迁移"""
        migrations = [
            "ALTER TABLE Event ADD participants STRING",
            "ALTER TABLE Event ADD time_range STRING",
            "ALTER TABLE Event ADD location STRING",
            "ALTER TABLE Event ADD action STRING",
            "ALTER TABLE Event ADD causality STRING",
            "ALTER TABLE Event ADD event_type STRING",
            "ALTER TABLE Event ADD payload STRING",
            "ALTER TABLE Event ADD evidence STRING",
            "ALTER TABLE Event ADD consistency STRING",
            "ALTER TABLE Event ADD timestamp INT64",
            "ALTER TABLE Event ADD last_active INT64",
            "ALTER TABLE Event ADD created_at INT64",
            "ALTER TABLE Event ADD updated_at INT64",
            "ALTER TABLE Event ADD valid_from INT64",
            "ALTER TABLE Event ADD valid_to INT64",
            "ALTER TABLE Event ADD salience DOUBLE",
            "ALTER TABLE Event ADD confidence DOUBLE",
            "ALTER TABLE Event ADD source STRING",
            "ALTER TABLE Event ADD status STRING",
            "ALTER TABLE Event ADD support_count INT64",
            "ALTER TABLE Entity ADD embedding FLOAT[1536]",
            "ALTER TABLE INVOLVES ADD t_expired INT64",
            "ALTER TABLE INVOLVES ADD t_valid INT64",
            "ALTER TABLE INVOLVES ADD t_invalid INT64",
            "ALTER TABLE INVOLVES ADD c_valid INT64",
            "ALTER TABLE Context ADD structured_slots STRING",
            "ALTER TABLE Context ADD confidence DOUBLE",
            "ALTER TABLE Context ADD support_count INT64",
            "ALTER TABLE Context ADD created_at INT64",
            "ALTER TABLE Context ADD updated_at INT64",
            "ALTER TABLE Context ADD valid_from INT64",
            "ALTER TABLE Context ADD valid_to INT64",
            "ALTER TABLE Context ADD last_seen_at INT64",
            "ALTER TABLE Context ADD status STRING",
            "ALTER TABLE Context ADD embedding FLOAT[1536]",
            "ALTER TABLE Pattern ADD prototype_features STRING",
            "ALTER TABLE Pattern ADD support_count INT64",
            "ALTER TABLE Pattern ADD confidence DOUBLE",
            "ALTER TABLE Pattern ADD stability_score DOUBLE",
            "ALTER TABLE Pattern ADD drift_score DOUBLE",
            "ALTER TABLE Pattern ADD created_at INT64",
            "ALTER TABLE Pattern ADD updated_at INT64",
            "ALTER TABLE Pattern ADD valid_from INT64",
            "ALTER TABLE Pattern ADD valid_to INT64",
            "ALTER TABLE Pattern ADD last_seen_at INT64",
            "ALTER TABLE Pattern ADD status STRING",
            "ALTER TABLE Pattern ADD embedding FLOAT[1536]",
            "ALTER TABLE IN_REL ADD original_type STRING",
            "ALTER TABLE IN_REL ADD confidence DOUBLE",
            "ALTER TABLE IN_REL ADD weight DOUBLE",
            "ALTER TABLE IN_REL ADD created_at INT64",
            "ALTER TABLE IN_REL ADD updated_at INT64",
            "ALTER TABLE IN_REL ADD last_seen_at INT64",
            "ALTER TABLE NEXT ADD confidence DOUBLE",
            "ALTER TABLE NEXT ADD score DOUBLE",
            "ALTER TABLE NEXT ADD relation_hint STRING",
            "ALTER TABLE NEXT ADD created_at INT64",
            "ALTER TABLE NEXT ADD updated_at INT64",
            "ALTER TABLE NEXT ADD last_seen_at INT64",
            "ALTER TABLE NEXT ADD support_count INT64",
            "ALTER TABLE ABSTRACT_TO ADD confidence DOUBLE",
            "ALTER TABLE ABSTRACT_TO ADD contribution_weight DOUBLE",
            "ALTER TABLE ABSTRACT_TO ADD created_at INT64",
            "ALTER TABLE ABSTRACT_TO ADD updated_at INT64",
            "ALTER TABLE ABSTRACT_TO ADD last_reinforced_at INT64",
            "ALTER TABLE EVENT_MERGE_TRACE ADD merge_reason STRING",
            "ALTER TABLE EVENT_MERGE_TRACE ADD similarity_score DOUBLE",
            "ALTER TABLE EVENT_MERGE_TRACE ADD merged_at INT64",
            "ALTER TABLE EVENT_MERGE_TRACE ADD strategy_version STRING",
        ]

        for stmt in migrations:
            try:
                self.conn.execute(stmt)
            except Exception:
                continue

    def _event_columns(self) -> list[str]:
        return [
            "id", "summary", "action", "causality", "time_range",
            "last_active", "participants", "location", "evidence",
            "consistency", "embedding", "event_type", "timestamp",
            "created_at", "updated_at", "valid_from", "valid_to",
            "salience", "confidence", "source", "status",
            "support_count", "payload",
        ]

    def _event_select_clause(self, alias: str = "e") -> str:
        return f"""
            {alias}.id, {alias}.summary, {alias}.action, {alias}.causality, {alias}.time_range,
            {alias}.last_active, {alias}.participants, {alias}.location, {alias}.evidence,
            {alias}.consistency, {alias}.embedding, {alias}.event_type, {alias}.timestamp,
            {alias}.created_at, {alias}.updated_at, {alias}.valid_from, {alias}.valid_to,
            {alias}.salience, {alias}.confidence, {alias}.source, {alias}.status,
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
            location=safe_json_loads(row[7], {}),
            evidence=safe_json_loads(row[8], []),
            consistency=row[9] or "uncertain",
            embedding=list(row[10]) if row[10] else None,
            event_type=row[11] or "generic",
            timestamp=row[12] or 0,
            created_at=row[13] or 0,
            updated_at=row[14] or 0,
            valid_from=row[15] or 0,
            valid_to=row[16],
            salience=float(row[17] or 0.5),
            confidence=float(row[18] or 0.7),
            source=row[19] or "llm_extraction",
            status=row[20] or "active",
            support_count=int(row[21] or 1),
            payload=safe_json_loads(row[22], {}),
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
                event_type: $event_type,
                summary: $summary,
                participants: $participants,
                time_range: $time_range,
                location: $location,
                action: $action,
                causality: $causality,
                payload: $payload,
                evidence: $evidence,
                consistency: $consistency,
                timestamp: $timestamp,
                last_active: $last_active,
                created_at: $created_at,
                updated_at: $updated_at,
                valid_from: $valid_from,
                valid_to: $valid_to,
                salience: $salience,
                confidence: $confidence,
                source: $source,
                status: $status,
                support_count: $support_count,
                embedding: $embedding
            })
            """,
            fields,
        )

    def get_event(self, event_id: str) -> Optional[Event]:
        """获取Event"""
        resp = self.conn.execute(
            """
            MATCH (e:Event {id: $id})
            RETURN e.id, e.summary, e.action, e.causality, e.time_range,
                   e.last_active, e.participants, e.location, e.evidence,
                   e.consistency, e.embedding, e.event_type, e.timestamp,
                   e.created_at, e.updated_at, e.valid_from, e.valid_to,
                   e.salience, e.confidence, e.source, e.status,
                   e.support_count, e.payload
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
                e.location = $location,
                e.action = $action,
                e.causality = $causality,
                e.event_type = $event_type,
                e.payload = $payload,
                e.evidence = $evidence,
                e.consistency = $consistency,
                e.timestamp = $timestamp,
                e.last_active = $last_active,
                e.created_at = $created_at,
                e.updated_at = $updated_at,
                e.valid_from = $valid_from,
                e.valid_to = $valid_to,
                e.salience = $salience,
                e.confidence = $confidence,
                e.source = $source,
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
                   e.last_active, e.participants, e.location, e.evidence,
                   e.consistency, e.embedding, e.event_type, e.timestamp,
                   e.created_at, e.updated_at, e.valid_from, e.valid_to,
                   e.salience, e.confidence, e.source, e.status,
                   e.support_count, e.payload
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
                   collect(en.id), e.status, e.confidence, e.salience, e.timestamp
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
                "confidence": float(row[7] or 0.7),
                "salience": float(row[8] or 0.5),
                "timestamp": row[9] or 0,
            })
        return events

    # ==================== Entity 操作 ====================

    def ensure_entity(self, entity_name: str, entity_type: str = "UNKNOWN") -> bool:
        """确保实体存在"""
        resp = self.conn.execute(
            "MATCH (e:Entity {id: $id}) RETURN count(*)",
            {"id": entity_name},
        )
        exists = resp.has_next() and resp.get_next()[0] > 0

        if not exists:
            # 生成嵌入（如果有客户端）
            embedding = None
            if self.embedding_client:
                embedding = self.embedding_client.get_embedding(entity_name)

            self.conn.execute(
                "CREATE (:Entity {id: $id, type: $type, embedding: $embedding})",
                {"id": entity_name, "type": entity_type, "embedding": embedding},
            )
            return True

        return False

    def get_all_entities(self) -> list[str]:
        """获取所有实体名称"""
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
                structured_slots: $structured_slots,
                confidence: $confidence,
                support_count: $support_count,
                created_at: $created_at,
                updated_at: $updated_at,
                valid_from: $valid_from,
                valid_to: $valid_to,
                last_seen_at: $last_seen_at,
                status: $status,
                embedding: $embedding
            })
            """,
            fields,
        )

    def get_context(self, context_id: str) -> Optional[Context]:
        resp = self.conn.execute(
            """
            MATCH (c:Context {id: $id})
            RETURN c.id, c.context_type, c.subtype, c.summary, c.structured_slots,
                   c.confidence, c.support_count, c.created_at, c.updated_at,
                   c.valid_from, c.valid_to, c.last_seen_at, c.status, c.embedding
            """,
            {"id": context_id},
        )
        if not resp.has_next():
            return None
        row = resp.get_next()
        cols = [
            "id", "context_type", "subtype", "summary", "structured_slots",
            "confidence", "support_count", "created_at", "updated_at",
            "valid_from", "valid_to", "last_seen_at", "status", "embedding",
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
                c.structured_slots = $structured_slots,
                c.confidence = $confidence,
                c.support_count = $support_count,
                c.updated_at = $updated_at,
                c.valid_from = $valid_from,
                c.valid_to = $valid_to,
                c.last_seen_at = $last_seen_at,
                c.status = $status,
                c.embedding = $embedding
            """,
            {
                "id": fields["id"],
                "context_type": fields["context_type"],
                "subtype": fields["subtype"],
                "summary": fields["summary"],
                "structured_slots": fields["structured_slots"],
                "confidence": fields["confidence"],
                "support_count": fields["support_count"],
                "updated_at": fields["updated_at"],
                "valid_from": fields["valid_from"],
                "valid_to": fields["valid_to"],
                "last_seen_at": fields["last_seen_at"],
                "status": fields["status"],
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
            RETURN c.id, c.context_type, c.subtype, c.summary, c.structured_slots,
                   c.confidence, c.support_count, c.created_at, c.updated_at,
                   c.valid_from, c.valid_to, c.last_seen_at, c.status, c.embedding
            ORDER BY c.last_seen_at DESC
            LIMIT $limit
            """,
            params,
        )
        cols = [
            "id", "context_type", "subtype", "summary", "structured_slots",
            "confidence", "support_count", "created_at", "updated_at",
            "valid_from", "valid_to", "last_seen_at", "status", "embedding",
        ]
        result = []
        while resp.has_next():
            result.append(Context.from_db_row(list(resp.get_next()), cols))
        return result

    def save_pattern(self, pattern: Pattern) -> None:
        self.conn.execute(
            """
            CREATE (:Pattern {
                id: $id,
                pattern_type: $pattern_type,
                summary: $summary,
                prototype_features: $prototype_features,
                support_count: $support_count,
                confidence: $confidence,
                stability_score: $stability_score,
                drift_score: $drift_score,
                created_at: $created_at,
                updated_at: $updated_at,
                valid_from: $valid_from,
                valid_to: $valid_to,
                last_seen_at: $last_seen_at,
                status: $status,
                embedding: $embedding
            })
            """,
            pattern.to_db_fields(),
        )

    def get_pattern(self, pattern_id: str) -> Optional[Pattern]:
        resp = self.conn.execute(
            """
            MATCH (p:Pattern {id: $id})
            RETURN p.id, p.pattern_type, p.summary, p.prototype_features,
                   p.support_count, p.confidence, p.stability_score, p.drift_score,
                   p.created_at, p.updated_at, p.valid_from, p.valid_to,
                   p.last_seen_at, p.status, p.embedding
            """,
            {"id": pattern_id},
        )
        if not resp.has_next():
            return None
        cols = [
            "id", "pattern_type", "summary", "prototype_features",
            "support_count", "confidence", "stability_score", "drift_score",
            "created_at", "updated_at", "valid_from", "valid_to",
            "last_seen_at", "status", "embedding",
        ]
        return Pattern.from_db_row(list(resp.get_next()), cols)

    def update_pattern(self, pattern: Pattern) -> None:
        fields = pattern.to_db_fields()
        self.conn.execute(
            """
            MATCH (p:Pattern {id: $id})
            SET p.pattern_type = $pattern_type,
                p.summary = $summary,
                p.prototype_features = $prototype_features,
                p.support_count = $support_count,
                p.confidence = $confidence,
                p.stability_score = $stability_score,
                p.drift_score = $drift_score,
                p.updated_at = $updated_at,
                p.valid_from = $valid_from,
                p.valid_to = $valid_to,
                p.last_seen_at = $last_seen_at,
                p.status = $status,
                p.embedding = $embedding
            """,
            {
                "id": fields["id"],
                "pattern_type": fields["pattern_type"],
                "summary": fields["summary"],
                "prototype_features": fields["prototype_features"],
                "support_count": fields["support_count"],
                "confidence": fields["confidence"],
                "stability_score": fields["stability_score"],
                "drift_score": fields["drift_score"],
                "updated_at": fields["updated_at"],
                "valid_from": fields["valid_from"],
                "valid_to": fields["valid_to"],
                "last_seen_at": fields["last_seen_at"],
                "status": fields["status"],
                "embedding": fields["embedding"],
            },
        )

    def find_pattern_candidates(
        self,
        pattern_type: str,
        limit: int = 20,
        only_active: bool = True,
    ) -> list[Pattern]:
        filters = ["p.pattern_type = $pattern_type"]
        params: dict[str, Any] = {"pattern_type": pattern_type, "limit": int(limit)}
        if only_active:
            filters.append("p.status = 'active'")
        where_clause = " AND ".join(filters)
        resp = self.conn.execute(
            f"""
            MATCH (p:Pattern)
            WHERE {where_clause}
            RETURN p.id, p.pattern_type, p.summary, p.prototype_features,
                   p.support_count, p.confidence, p.stability_score, p.drift_score,
                   p.created_at, p.updated_at, p.valid_from, p.valid_to,
                   p.last_seen_at, p.status, p.embedding
            ORDER BY p.last_seen_at DESC
            LIMIT $limit
            """,
            params,
        )
        cols = [
            "id", "pattern_type", "summary", "prototype_features",
            "support_count", "confidence", "stability_score", "drift_score",
            "created_at", "updated_at", "valid_from", "valid_to",
            "last_seen_at", "status", "embedding",
        ]
        result = []
        while resp.has_next():
            result.append(Pattern.from_db_row(list(resp.get_next()), cols))
        return result

    def link_event_to_context(
        self,
        event_id: str,
        context_id: str,
        confidence: float,
        weight: float,
        original_type: str,
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
                    r.original_type = $original_type
                """,
                {
                    "event_id": event_id,
                    "context_id": context_id,
                    "confidence": float(confidence),
                    "weight": float(weight),
                    "timestamp": int(timestamp),
                    "original_type": original_type,
                },
            )
            return

        self.conn.execute(
            """
            MATCH (e:Event {id: $event_id}), (c:Context {id: $context_id})
            CREATE (e)-[:IN_REL {
                confidence: $confidence,
                weight: $weight,
                original_type: $original_type,
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
                "original_type": original_type,
            },
        )

    def link_next(
        self,
        from_event_id: str,
        to_event_id: str,
        confidence: float,
        score: float,
        relation_hint: str,
        timestamp: int,
    ) -> None:
        if from_event_id == to_event_id:
            return
        exists = self.conn.execute(
            """
            MATCH (:Event {id: $from_event_id})-[r:NEXT]->(:Event {id: $to_event_id})
            RETURN r.support_count
            """,
            {"from_event_id": from_event_id, "to_event_id": to_event_id},
        )
        if exists.has_next():
            support_count = exists.get_next()[0] or 1
            self.conn.execute(
                """
                MATCH (:Event {id: $from_event_id})-[r:NEXT]->(:Event {id: $to_event_id})
                SET r.confidence = $confidence,
                    r.score = $score,
                    r.relation_hint = $relation_hint,
                    r.updated_at = $timestamp,
                    r.last_seen_at = $timestamp,
                    r.support_count = $support_count
                """,
                {
                    "from_event_id": from_event_id,
                    "to_event_id": to_event_id,
                    "confidence": float(confidence),
                    "score": float(score),
                    "relation_hint": relation_hint,
                    "timestamp": int(timestamp),
                    "support_count": int(support_count) + 1,
                },
            )
            return

        self.conn.execute(
            """
            MATCH (a:Event {id: $from_event_id}), (b:Event {id: $to_event_id})
            CREATE (a)-[:NEXT {
                confidence: $confidence,
                score: $score,
                relation_hint: $relation_hint,
                created_at: $timestamp,
                updated_at: $timestamp,
                last_seen_at: $timestamp,
                support_count: 1
            }]->(b)
            """,
            {
                "from_event_id": from_event_id,
                "to_event_id": to_event_id,
                "confidence": float(confidence),
                "score": float(score),
                "relation_hint": relation_hint,
                "timestamp": int(timestamp),
            },
        )

    def link_event_to_pattern(
        self,
        event_id: str,
        pattern_id: str,
        confidence: float,
        contribution_weight: float,
        timestamp: int,
    ) -> None:
        exists = self.conn.execute(
            """
            MATCH (:Event {id: $event_id})-[r:ABSTRACT_TO]->(:Pattern {id: $pattern_id})
            RETURN count(*)
            """,
            {"event_id": event_id, "pattern_id": pattern_id},
        )
        found = exists.has_next() and exists.get_next()[0] > 0
        if found:
            self.conn.execute(
                """
                MATCH (:Event {id: $event_id})-[r:ABSTRACT_TO]->(:Pattern {id: $pattern_id})
                SET r.confidence = $confidence,
                    r.contribution_weight = $contribution_weight,
                    r.updated_at = $timestamp,
                    r.last_reinforced_at = $timestamp
                """,
                {
                    "event_id": event_id,
                    "pattern_id": pattern_id,
                    "confidence": float(confidence),
                    "contribution_weight": float(contribution_weight),
                    "timestamp": int(timestamp),
                },
            )
            return

        self.conn.execute(
            """
            MATCH (e:Event {id: $event_id}), (p:Pattern {id: $pattern_id})
            CREATE (e)-[:ABSTRACT_TO {
                confidence: $confidence,
                contribution_weight: $contribution_weight,
                created_at: $timestamp,
                updated_at: $timestamp,
                last_reinforced_at: $timestamp
            }]->(p)
            """,
            {
                "event_id": event_id,
                "pattern_id": pattern_id,
                "confidence": float(confidence),
                "contribution_weight": float(contribution_weight),
                "timestamp": int(timestamp),
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
            """
            MATCH (e:Event)
            WHERE e.last_active >= $min_ts
            RETURN e.id, e.summary, e.action, e.causality, e.time_range,
                   e.last_active, e.participants, e.location, e.evidence,
                   e.consistency, e.embedding, e.event_type, e.timestamp,
                   e.created_at, e.updated_at, e.valid_from, e.valid_to,
                   e.salience, e.confidence, e.source, e.status,
                   e.support_count, e.payload
            ORDER BY e.last_active DESC
            LIMIT $limit
            """,
            {"min_ts": min_ts, "limit": int(limit)},
        )
        events = []
        while resp.has_next():
            events.append(self._row_to_event(list(resp.get_next())))
        return events

    def get_event_contexts(self, event_id: str) -> list[Context]:
        resp = self.conn.execute(
            """
            MATCH (:Event {id: $event_id})-[r:IN_REL]->(c:Context)
            RETURN c.id, c.context_type, c.subtype, c.summary, c.structured_slots,
                   c.confidence, c.support_count, c.created_at, c.updated_at,
                   c.valid_from, c.valid_to, c.last_seen_at, c.status, c.embedding
            ORDER BY r.weight DESC, r.confidence DESC
            """,
            {"event_id": event_id},
        )
        cols = [
            "id", "context_type", "subtype", "summary", "structured_slots",
            "confidence", "support_count", "created_at", "updated_at",
            "valid_from", "valid_to", "last_seen_at", "status", "embedding",
        ]
        result = []
        while resp.has_next():
            result.append(Context.from_db_row(list(resp.get_next()), cols))
        return result

    def get_event_patterns(self, event_id: str) -> list[Pattern]:
        resp = self.conn.execute(
            """
            MATCH (:Event {id: $event_id})-[r:ABSTRACT_TO]->(p:Pattern)
            RETURN p.id, p.pattern_type, p.summary, p.prototype_features,
                   p.support_count, p.confidence, p.stability_score, p.drift_score,
                   p.created_at, p.updated_at, p.valid_from, p.valid_to,
                   p.last_seen_at, p.status, p.embedding
            ORDER BY r.contribution_weight DESC, p.confidence DESC
            """,
            {"event_id": event_id},
        )
        cols = [
            "id", "pattern_type", "summary", "prototype_features",
            "support_count", "confidence", "stability_score", "drift_score",
            "created_at", "updated_at", "valid_from", "valid_to",
            "last_seen_at", "status", "embedding",
        ]
        result = []
        while resp.has_next():
            result.append(Pattern.from_db_row(list(resp.get_next()), cols))
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
            RETURN c.id, c.context_type, c.subtype, c.summary, c.structured_slots,
                   c.confidence, c.support_count, c.created_at, c.updated_at,
                   c.valid_from, c.valid_to, c.last_seen_at, c.status, c.embedding
            ORDER BY c.last_seen_at DESC
            LIMIT $limit
            """,
            {"limit": int(max(limit * 4, limit))},
        )
        cols = [
            "id", "context_type", "subtype", "summary", "structured_slots",
            "confidence", "support_count", "created_at", "updated_at",
            "valid_from", "valid_to", "last_seen_at", "status", "embedding",
        ]
        scored: list[tuple[float, Context]] = []
        while resp.has_next():
            context = Context.from_db_row(list(resp.get_next()), cols)
            haystack = f"{context.summary} {safe_json_dumps(context.structured_slots)}".lower()
            score = self._text_similarity_score(haystack, query, query_entities)
            if score <= 0.0 and (query or query_entities):
                continue
            score += 0.15 * context.confidence
            scored.append((score, context))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [context for _, context in scored[:limit]]

    def retrieve_candidate_patterns_for_query(
        self,
        query: str,
        query_entities: list[str],
        limit: int = 20,
    ) -> list[Pattern]:
        resp = self.conn.execute(
            """
            MATCH (p:Pattern)
            WHERE p.status = 'active'
            RETURN p.id, p.pattern_type, p.summary, p.prototype_features,
                   p.support_count, p.confidence, p.stability_score, p.drift_score,
                   p.created_at, p.updated_at, p.valid_from, p.valid_to,
                   p.last_seen_at, p.status, p.embedding
            ORDER BY p.last_seen_at DESC
            LIMIT $limit
            """,
            {"limit": int(max(limit * 4, limit))},
        )
        cols = [
            "id", "pattern_type", "summary", "prototype_features",
            "support_count", "confidence", "stability_score", "drift_score",
            "created_at", "updated_at", "valid_from", "valid_to",
            "last_seen_at", "status", "embedding",
        ]
        scored: list[tuple[float, Pattern]] = []
        while resp.has_next():
            pattern = Pattern.from_db_row(list(resp.get_next()), cols)
            haystack = f"{pattern.summary} {safe_json_dumps(pattern.prototype_features)}".lower()
            score = self._text_similarity_score(haystack, query, query_entities)
            if score <= 0.0 and (query or query_entities):
                continue
            score += 0.15 * pattern.confidence
            scored.append((score, pattern))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [pattern for _, pattern in scored[:limit]]

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

    def retrieve_events_by_patterns(
        self,
        pattern_ids: list[str],
        limit: int = 50,
    ) -> list[Event]:
        if not pattern_ids:
            return []
        resp = self.conn.execute(
            f"""
            MATCH (e:Event)-[r:ABSTRACT_TO]->(p:Pattern)
            WHERE p.id IN $pattern_ids
            RETURN DISTINCT {self._event_select_clause('e')}
            ORDER BY e.last_active DESC
            LIMIT $limit
            """,
            {"pattern_ids": pattern_ids, "limit": int(limit)},
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

    def prune_weak_next_edges(self, min_score: float, stale_before: int) -> int:
        count_resp = self.conn.execute(
            """
            MATCH (:Event)-[r:NEXT]->(:Event)
            WHERE r.score < $min_score AND r.last_seen_at < $stale_before
            RETURN count(r)
            """,
            {"min_score": float(min_score), "stale_before": int(stale_before)},
        )
        count = count_resp.get_next()[0] if count_resp.has_next() else 0
        self.conn.execute(
            """
            MATCH (:Event)-[r:NEXT]->(:Event)
            WHERE r.score < $min_score AND r.last_seen_at < $stale_before
            DELETE r
            """,
            {"min_score": float(min_score), "stale_before": int(stale_before)},
        )
        return int(count)

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

        # 统计 Pattern
        resp = self.conn.execute("MATCH (p:Pattern) RETURN count(*)")
        stats["pattern_count"] = resp.get_next()[0] if resp.has_next() else 0

        # 统计 INVOLVES 关系
        resp = self.conn.execute("MATCH ()-[r:INVOLVES]->() RETURN count(r)")
        stats["involves_count"] = resp.get_next()[0] if resp.has_next() else 0

        resp = self.conn.execute("MATCH ()-[r:IN_REL]->() RETURN count(r)")
        stats["in_count"] = resp.get_next()[0] if resp.has_next() else 0

        resp = self.conn.execute("MATCH ()-[r:NEXT]->() RETURN count(r)")
        stats["next_count"] = resp.get_next()[0] if resp.has_next() else 0

        resp = self.conn.execute("MATCH ()-[r:ABSTRACT_TO]->() RETURN count(r)")
        stats["abstract_to_count"] = resp.get_next()[0] if resp.has_next() else 0

        resp = self.conn.execute("MATCH ()-[r:EVENT_MERGE_TRACE]->() RETURN count(r)")
        stats["event_merge_trace_count"] = resp.get_next()[0] if resp.has_next() else 0

        return stats
