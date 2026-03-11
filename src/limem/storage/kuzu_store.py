# -*- coding: utf-8 -*-
"""KuzuStore - Kuzu 图数据库实现

实现 GraphStore 接口的 Kuzu 具体实现。
"""

import os
from typing import Any, Optional

import kuzu

from ..core.episode import Episode
from ..core.event import Event, EventRelation
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
                summary STRING,
                participants STRING,
                time_range STRING,
                location STRING,
                action STRING,
                causality STRING,
                evidence STRING,
                consistency STRING,
                last_active INT64,
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
            "ALTER TABLE Event ADD evidence STRING",
            "ALTER TABLE Event ADD consistency STRING",
            "ALTER TABLE Event ADD last_active INT64",
            "ALTER TABLE Entity ADD embedding FLOAT[1536]",
            "ALTER TABLE INVOLVES ADD t_expired INT64",
            "ALTER TABLE INVOLVES ADD t_valid INT64",
            "ALTER TABLE INVOLVES ADD t_invalid INT64",
            "ALTER TABLE INVOLVES ADD c_valid INT64",
        ]

        for stmt in migrations:
            try:
                self.conn.execute(stmt)
            except Exception:
                continue

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
                location: $location,
                action: $action,
                causality: $causality,
                evidence: $evidence,
                consistency: $consistency,
                last_active: $last_active,
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
                   e.consistency, e.embedding
            """,
            {"id": event_id},
        )
        if resp.has_next():
            row = resp.get_next()
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
            )
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
                e.evidence = $evidence,
                e.consistency = $consistency,
                e.last_active = $last_active,
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
                   e.consistency, e.embedding
        """
        resp = self.conn.execute(query, {"entities": entities})

        events = []
        while resp.has_next():
            row = resp.get_next()
            events.append(Event(
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
            ))
        return events

    def get_all_events_with_entities(self) -> list[dict[str, Any]]:
        """获取所有事件及其关联实体"""
        query = """
            MATCH (e:Event)-[r:INVOLVES]->(en:Entity)
            RETURN e.id, e.summary, e.embedding, e.action, e.last_active, collect(en.id)
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

        # 统计 INVOLVES 关系
        resp = self.conn.execute("MATCH ()-[r:INVOLVES]->() RETURN count(r)")
        stats["involves_count"] = resp.get_next()[0] if resp.has_next() else 0

        return stats
