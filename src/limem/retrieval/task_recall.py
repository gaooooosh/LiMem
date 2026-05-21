# -*- coding: utf-8 -*-
"""Graph-guided task recall for agent-facing prompt memory.

This module treats recall as compiling useful memory for the current task, not
as a user-facing search endpoint. It uses deterministic graph evidence only:
no LLM calls and no task-type word lists.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional
import math
import re
import time

from ..core.context import Context
from ..core.entity import Entity
from ..core.event import Event
from ..evolution.relation_types import REL_MEANING_UPDATE, REL_SAME_TOPIC, REL_SHARED_CONTEXT
from ..evolution.relation_types import event_relation_query_types, normalize_event_relation_type
from ..utils import safe_json_dumps
from .pattern_recall import recall_pattern
from .bm25 import tokenize


_URL_RE = re.compile(r"https?://[^\s，。；;]+")
_PATH_RE = re.compile(r"(?:~|\.{1,2}|/)[\w./:@%+\-=]+")
_ENV_RE = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")
_VERSION_RE = re.compile(r"\bv?\d+(?:\.\d+){1,3}(?:[-_][A-Za-z0-9.]+)?\b")
_PORT_RE = re.compile(r"(?<![\w.])\d{2,5}(?![\w.])")
_NUMBER_UNIT_RE = re.compile(r"\d+(?:\.\d+)?\s?(?:度|℃|%|ms|s|秒|分钟|小时|天|MB|GB|kb|KB|元|次)")
_QUOTED_RE = re.compile(r"[`\"'“”‘’]([^`\"'“”‘’]{2,120})[`\"'“”‘’]")
_CODE_TOKEN_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b")
_CHINESE_COMPACT_RE = re.compile(r"[\u4e00-\u9fff]{2,}")


@dataclass
class AnchorHit:
    kind: str
    target_type: str
    target_id: str
    matched_terms: list[str] = field(default_factory=list)
    evidence: str = ""

    def strength(self) -> int:
        return {"literal": 4, "entity": 3, "context": 2, "event": 2, "lexical": 1}.get(self.kind, 0)


@dataclass
class TaskProjection:
    task: str
    literal_anchors: list[str] = field(default_factory=list)
    lexical_anchors: list[str] = field(default_factory=list)
    entity_anchors: list[AnchorHit] = field(default_factory=list)
    context_anchors: list[AnchorHit] = field(default_factory=list)
    event_anchors: list[AnchorHit] = field(default_factory=list)
    token_df: dict[str, int] = field(default_factory=dict)

    def all_anchor_terms(self) -> list[str]:
        terms: list[str] = []
        for group in (self.literal_anchors, self.lexical_anchors):
            for item in group:
                if item and item not in terms:
                    terms.append(item)
        for hit in self.entity_anchors + self.context_anchors + self.event_anchors:
            for item in hit.matched_terms:
                if item and item not in terms:
                    terms.append(item)
        return terms


@dataclass
class MemoryPath:
    path_type: str
    memory_type: str
    anchor_terms: list[str] = field(default_factory=list)
    nodes: list[Any] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    path_length: int = 1
    source: str = ""
    direct_literal_hits: int = 0
    direct_entity_hits: int = 0
    support_count: int = 1
    last_active: int = 0
    folded_text: str = ""
    folded_reason: str = ""
    warning: bool = False

    def primary(self) -> Any:
        return self.nodes[0] if self.nodes else None

    def key(self) -> str:
        primary = self.primary()
        primary_id = getattr(primary, "id", None)
        if primary_id:
            return f"{self.memory_type}:{primary_id}"
        return f"{self.memory_type}:{self.folded_text}"


@dataclass
class RecallItem:
    kind: str
    text: str
    source_path: MemoryPath
    tier: int

    def to_debug_dict(self) -> dict[str, Any]:
        path = self.source_path
        return {
            "kind": self.kind,
            "text": self.text,
            "path_type": path.path_type,
            "anchor_terms": list(path.anchor_terms),
            "relations": list(path.relations),
            "folded_reason": path.folded_reason,
        }


class TaskProjector:
    """Project a task into literal, lexical, and graph-derived anchors."""

    def __init__(self, store: Any, candidate_limit: int = 240):
        self.store = store
        self.candidate_limit = max(20, int(candidate_limit))

    def project(self, task: str) -> TaskProjection:
        text = str(task or "").strip()
        literal_anchors = self._literal_anchors(text)
        documents = self._memory_documents()
        token_df = self._document_frequency(documents)
        lexical_anchors = self._lexical_anchors(text, token_df, len(documents))
        projection = TaskProjection(
            task=text,
            literal_anchors=literal_anchors,
            lexical_anchors=lexical_anchors,
            token_df=token_df,
        )
        self._attach_graph_anchors(projection)
        return projection

    def _literal_anchors(self, text: str) -> list[str]:
        anchors: list[str] = []
        for pattern in (
            _URL_RE,
            _PATH_RE,
            _ENV_RE,
            _VERSION_RE,
            _NUMBER_UNIT_RE,
            _CODE_TOKEN_RE,
        ):
            for match in pattern.finditer(text):
                self._append_unique(anchors, match.group(0).strip())
        for match in _QUOTED_RE.finditer(text):
            self._append_unique(anchors, match.group(1).strip())
        # Ports and standalone numbers are useful only when not already inside a richer literal.
        for match in _PORT_RE.finditer(text):
            value = match.group(0).strip()
            if any(value in existing and value != existing for existing in anchors):
                continue
            self._append_unique(anchors, value)
        return anchors

    def _memory_documents(self) -> list[str]:
        docs: list[str] = []
        try:
            for event in self.store.list_events(limit=self.candidate_limit, statuses=["active"]):
                docs.append(_event_text(event))
        except Exception:
            pass
        try:
            for context in self.store.list_contexts(limit=self.candidate_limit, statuses=["active"]):
                docs.append(_context_text(context))
        except Exception:
            pass
        try:
            for entity in self.store.list_registered_entities_with_embeddings():
                docs.append(_entity_text(entity))
                pattern = self.store.get_entity_pattern(entity.id)
                if pattern:
                    docs.append(str(pattern.get("content", "") or ""))
        except Exception:
            pass
        return [doc for doc in docs if doc.strip()]

    def _document_frequency(self, docs: list[str]) -> dict[str, int]:
        df: Counter[str] = Counter()
        for doc in docs:
            df.update(set(_normalized_tokens(doc)))
        return dict(df)

    def _lexical_anchors(self, text: str, df: dict[str, int], document_count: int) -> list[str]:
        candidates = _normalized_tokens(text)
        if not candidates:
            return []
        scored: list[tuple[float, int, str]] = []
        for token in set(candidates):
            if token in df:
                rarity = math.log(1.0 + (document_count + 1) / (df[token] + 1))
            else:
                rarity = math.log(1.0 + document_count + 1)
            # Keep graph-local rare terms first. Length is a deterministic tie-breaker,
            # not a tunable relevance weight.
            scored.append((rarity, len(token), token))
        scored.sort(reverse=True)
        return [token for _, _, token in scored[:12]]

    def _attach_graph_anchors(self, projection: TaskProjection) -> None:
        terms = projection.literal_anchors + projection.lexical_anchors
        graph_payload: dict[str, Any] = {}
        try:
            graph_payload = self.store.project_task_anchors(
                task_text=projection.task,
                literal_anchors=projection.literal_anchors,
                lexical_anchors=projection.lexical_anchors,
            )
        except Exception:
            graph_payload = {}
        try:
            entities = graph_payload.get("entities")
            if entities is None:
                entities = self.store.list_registered_entities_with_embeddings()
            for entity in entities:
                matched = _matched_terms(_entity_text(entity), terms)
                if matched:
                    projection.entity_anchors.append(
                        AnchorHit(
                            kind="entity",
                            target_type="entity",
                            target_id=entity.id,
                            matched_terms=matched,
                            evidence=entity.description or entity.id,
                        )
                    )
        except Exception:
            pass

        try:
            contexts = graph_payload.get("contexts")
            if contexts is None:
                contexts = self.store.list_contexts(limit=self.candidate_limit, statuses=["active"])
            for context in contexts:
                matched = _matched_terms(_context_text(context), terms)
                if matched:
                    projection.context_anchors.append(
                        AnchorHit(
                            kind="context",
                            target_type="context",
                            target_id=context.id,
                            matched_terms=matched,
                            evidence=context.condition or context.summary,
                        )
                    )
        except Exception:
            pass

        try:
            events = graph_payload.get("events")
            if events is None:
                events = self.store.list_events(limit=self.candidate_limit, statuses=["active"])
            for event in events:
                matched = _matched_terms(_event_text(event), terms)
                if matched:
                    projection.event_anchors.append(
                        AnchorHit(
                            kind="event",
                            target_type="event",
                            target_id=event.id,
                            matched_terms=matched,
                            evidence=event.summary,
                        )
                    )
        except Exception:
            pass

    @staticmethod
    def _append_unique(items: list[str], value: str) -> None:
        value = str(value or "").strip(" ，,。；;")
        if value and value not in items:
            items.append(value)


class GraphTaskWalker:
    """Walk graph paths from projected anchors."""

    def __init__(self, store: Any, limit_per_anchor: int = 20):
        self.store = store
        self.limit_per_anchor = max(1, int(limit_per_anchor))

    def walk(self, projection: TaskProjection) -> list[MemoryPath]:
        try:
            raw_paths = self.store.walk_task_memory_paths(
                projection=projection,
                limit_per_anchor=self.limit_per_anchor,
            )
            if raw_paths is not None:
                return [path for path in raw_paths if isinstance(path, MemoryPath)]
        except NotImplementedError:
            pass
        except Exception:
            pass
        return self._fallback_walk(projection)

    def _fallback_walk(self, projection: TaskProjection) -> list[MemoryPath]:
        paths: list[MemoryPath] = []
        terms = projection.all_anchor_terms()
        now = int(time.time())
        entity_ids = [hit.target_id for hit in projection.entity_anchors]

        for entity_id in entity_ids:
            try:
                pattern = self.store.get_entity_pattern(entity_id)
            except Exception:
                pattern = None
            if pattern:
                section = recall_pattern(
                    str(pattern.get("content", "") or ""),
                    query=projection.task,
                    mode="section",
                    top_k_sections=1,
                )
                content = str(section.get("content", "") or "").strip()
                if content:
                    paths.append(
                        MemoryPath(
                            path_type="rule",
                            memory_type="rule",
                            anchor_terms=[entity_id],
                            nodes=[{"entity_id": entity_id, "pattern": pattern, "section": content}],
                            path_length=2,
                            source="entity_pattern",
                            direct_entity_hits=1,
                            last_active=int(pattern.get("updated_at") or pattern.get("created_at") or now),
                        )
                    )

        for context in self._contexts():
            matched = _matched_terms(_context_text(context), terms)
            if not matched:
                continue
            paths.append(
                MemoryPath(
                    path_type="context",
                    memory_type="context",
                    anchor_terms=matched,
                    nodes=[context],
                    path_length=1,
                    source="context_text",
                    direct_literal_hits=_literal_hit_count(matched, projection.literal_anchors),
                    support_count=int(context.support_count or 1),
                    last_active=int(context.last_seen_at or context.updated_at or context.created_at or 0),
                )
            )

        for event in self._events():
            matched = _matched_terms(_event_text(event), terms)
            if not matched:
                continue
            paths.append(
                MemoryPath(
                    path_type="event",
                    memory_type="event",
                    anchor_terms=matched,
                    nodes=[event],
                    path_length=1,
                    source="event_text",
                    direct_literal_hits=_literal_hit_count(matched, projection.literal_anchors),
                    direct_entity_hits=self._event_entity_hit_count(event, entity_ids),
                    support_count=int(event.support_count or 1),
                    last_active=int(event.last_active or event.timestamp or 0),
                )
            )

        for event_id in {hit.target_id for hit in projection.event_anchors}:
            paths.extend(self._relation_paths(event_id, projection))
        return paths

    def _events(self) -> list[Event]:
        try:
            return list(self.store.list_events(limit=self.limit_per_anchor * 20, statuses=["active"]))
        except Exception:
            return []

    def _contexts(self) -> list[Context]:
        try:
            return list(self.store.list_contexts(limit=self.limit_per_anchor * 20, statuses=["active"]))
        except Exception:
            return []

    def _relation_paths(self, event_id: str, projection: TaskProjection) -> list[MemoryPath]:
        try:
            edges = self.store.get_event_relation_paths(
                event_ids=[event_id],
                relation_types=event_relation_query_types(),
                depth=1,
            )
        except Exception:
            edges = []
        paths: list[MemoryPath] = []
        for edge in edges:
            src = edge.get("source_event")
            dst = edge.get("target_event")
            if not isinstance(src, Event) or not isinstance(dst, Event):
                continue
            relation = normalize_event_relation_type(edge.get("relation_type", ""))
            if not relation:
                continue
            edge["relation_type"] = relation
            paths.append(
                MemoryPath(
                    path_type="evolution",
                    memory_type="event",
                    anchor_terms=_matched_terms(
                        _event_text(src) + " " + _event_text(dst),
                        projection.all_anchor_terms(),
                    ),
                    nodes=[src, dst],
                    relations=[edge],
                    path_length=2,
                    source="event_relation",
                    support_count=max(int(src.support_count or 1), int(dst.support_count or 1)),
                    last_active=max(int(src.last_active or src.timestamp or 0), int(dst.last_active or dst.timestamp or 0)),
                    warning=relation == REL_MEANING_UPDATE,
                )
            )
        return paths

    def _event_entity_hit_count(self, event: Event, entity_ids: list[str]) -> int:
        if not entity_ids:
            return 0
        try:
            linked = set(self.store.get_event_entities(event.id))
        except Exception:
            linked = set()
        return len(linked & set(entity_ids))


class MemoryPathFolder:
    """Fold graph paths into compact action-relevant conclusions."""

    def fold(self, paths: list[MemoryPath]) -> list[MemoryPath]:
        superseded_event_ids = self._superseded_event_ids(paths)
        folded: list[MemoryPath] = []
        for path in paths:
            clone = _clone_path(path)
            if clone.memory_type == "rule":
                clone.folded_text = _pattern_section_text(clone.primary())
                clone.folded_reason = "rule"
            elif clone.memory_type == "context":
                clone.folded_text = _context_card_text(clone.primary())
                clone.folded_reason = "context"
            else:
                self._fold_event_path(clone)
            primary = clone.primary()
            if (
                clone.memory_type == "event"
                and isinstance(primary, Event)
                and primary.id in superseded_event_ids
                and clone.folded_reason not in {REL_SAME_TOPIC, REL_MEANING_UPDATE}
            ):
                continue
            if clone.folded_text:
                folded.append(clone)
        return self._dedupe(folded)

    def _superseded_event_ids(self, paths: list[MemoryPath]) -> set[str]:
        event_ids: set[str] = set()
        for path in paths:
            for relation in path.relations:
                relation_type = normalize_event_relation_type(relation.get("relation_type", ""))
                if relation_type not in {REL_SAME_TOPIC, REL_MEANING_UPDATE}:
                    continue
                source = relation.get("source_event")
                target = relation.get("target_event")
                if isinstance(source, Event) and isinstance(target, Event) and source.id != target.id:
                    event_ids.add(source.id)
        return event_ids

    def _fold_event_path(self, path: MemoryPath) -> None:
        events = [node for node in path.nodes if isinstance(node, Event)]
        if not events:
            return
        primary = events[0]
        relation = path.relations[0] if path.relations else {}
        relation_type = normalize_event_relation_type(relation.get("relation_type", ""))
        if relation_type in {REL_SAME_TOPIC, REL_MEANING_UPDATE} and len(events) > 1:
            primary = events[-1]
            path.nodes = [primary]
            path.folded_reason = relation_type
        elif relation_type == REL_SHARED_CONTEXT and len(events) > 1:
            path.folded_text = _join_clause(_event_card_text(events[0]), _event_card_text(events[1]))
            path.folded_reason = REL_SHARED_CONTEXT
            return
        path.folded_text = _event_card_text(primary)
        if not path.folded_reason:
            path.folded_reason = "event"

    def _dedupe(self, paths: list[MemoryPath]) -> list[MemoryPath]:
        by_key: dict[str, MemoryPath] = {}
        for path in paths:
            key = path.key()
            existing = by_key.get(key)
            if existing is None or _path_rank_tuple(path) > _path_rank_tuple(existing):
                by_key[key] = path
        return list(by_key.values())


class ActionValueSelector:
    """Select memories that can change the agent's current action."""

    _TIER = {"rule": 1, "warning": 1, "context": 2, "event": 3}

    def select(self, paths: list[MemoryPath], limit: int = 5) -> list[MemoryPath]:
        meaningful = [path for path in paths if self._has_action_value(path)]
        meaningful.sort(key=self._sort_key)
        selected: list[MemoryPath] = []
        seen_text: set[str] = set()
        for path in meaningful:
            text_key = _normalize_key(path.folded_text)
            if not text_key or text_key in seen_text:
                continue
            selected.append(path)
            seen_text.add(text_key)
            if len(selected) >= max(1, int(limit)):
                break
        return selected

    def _has_action_value(self, path: MemoryPath) -> bool:
        if not path.folded_text:
            return False
        if path.memory_type == "rule":
            return True
        if path.memory_type == "context":
            node = path.primary()
            return isinstance(node, Context) and bool(
                path.anchor_terms or node.facts or node.applies_when or node.condition
            )
        if path.memory_type == "event":
            return bool(
                path.anchor_terms
                or path.direct_entity_hits
                or path.direct_literal_hits
                or path.relations
            )
        return False

    def _sort_key(self, path: MemoryPath) -> tuple[Any, ...]:
        kind = "warning" if path.warning else path.memory_type
        return (
            self._TIER.get(kind, 9),
            -int(path.direct_literal_hits or 0),
            -int(path.direct_entity_hits or 0),
            int(path.path_length or 1),
            -int(path.support_count or 1),
            -int(path.last_active or 0),
        )


class TaskMemoryCompiler:
    """Compile selected memory paths into lightweight Markdown."""

    def compile(self, paths: list[MemoryPath], limit: int = 5) -> tuple[str, list[RecallItem]]:
        items: list[RecallItem] = []
        for path in paths[: max(1, int(limit))]:
            kind = "Warning" if path.warning else _display_kind(path.memory_type)
            text = _trim_line(path.folded_text, limit=120)
            if not text:
                continue
            items.append(
                RecallItem(
                    kind=kind,
                    text=f"- [{kind}] {text}",
                    source_path=path,
                    tier={"Rule": 1, "Warning": 1, "Context": 2, "Event": 3}.get(kind, 9),
                )
            )
        if not items:
            return "", []
        return "## Relevant Memory\n" + "\n".join(item.text for item in items), items


class TaskRecallPipeline:
    """End-to-end task recall pipeline."""

    def __init__(self, store: Any, candidate_limit: int = 240, limit_per_anchor: int = 20):
        self.projector = TaskProjector(store, candidate_limit=candidate_limit)
        self.walker = GraphTaskWalker(store, limit_per_anchor=limit_per_anchor)
        self.folder = MemoryPathFolder()
        self.selector = ActionValueSelector()
        self.compiler = TaskMemoryCompiler()

    def recall_for_task(
        self,
        task: str,
        limit: int = 5,
        include_debug: bool = False,
    ) -> dict[str, Any]:
        task = str(task or "").strip()
        if not task:
            return {"prompt_text": "", "items": [], "stats": {"reason": "empty_task"}}
        projection = self.projector.project(task)
        paths = self.walker.walk(projection)
        folded = self.folder.fold(paths)
        selected = self.selector.select(folded, limit=limit)
        prompt_text, items = self.compiler.compile(selected, limit=limit)
        result: dict[str, Any] = {
            "prompt_text": prompt_text,
            "items": [],
            "stats": {
                "literal_anchors": len(projection.literal_anchors),
                "lexical_anchors": len(projection.lexical_anchors),
                "entity_anchors": len(projection.entity_anchors),
                "context_anchors": len(projection.context_anchors),
                "event_anchors": len(projection.event_anchors),
                "paths": len(paths),
                "folded": len(folded),
                "selected": len(selected),
            },
        }
        if include_debug:
            result["projection"] = {
                "literal_anchors": projection.literal_anchors,
                "lexical_anchors": projection.lexical_anchors,
                "entity_anchors": [hit.__dict__ for hit in projection.entity_anchors],
                "context_anchors": [hit.__dict__ for hit in projection.context_anchors],
                "event_anchors": [hit.__dict__ for hit in projection.event_anchors],
            }
            result["items"] = [item.to_debug_dict() for item in items]
        return result


def _normalized_tokens(text: str) -> list[str]:
    raw = str(text or "")
    tokens = [token.strip().lower() for token in tokenize(raw) if token.strip()]
    for match in _CHINESE_COMPACT_RE.finditer(raw):
        compact = match.group(0)
        if len(compact) >= 2:
            tokens.extend(compact[idx: idx + 2] for idx in range(len(compact) - 1))
    return [token for token in tokens if _informative_token(token)]


def _informative_token(token: str) -> bool:
    if not token:
        return False
    if token.isdigit() and len(token) < 2:
        return False
    if re.fullmatch(r"[\u4e00-\u9fff]", token):
        return False
    return True


def _matched_terms(text: str, terms: Iterable[str]) -> list[str]:
    haystack = str(text or "").lower()
    hay_tokens = set(_normalized_tokens(haystack))
    matched: list[str] = []
    for term in terms:
        raw = str(term or "").strip()
        if not raw:
            continue
        lowered = raw.lower()
        if lowered in haystack:
            matched.append(raw)
            continue
        term_tokens = set(_normalized_tokens(raw))
        if term_tokens and hay_tokens & term_tokens:
            matched.append(raw)
    return _unique(matched)


def _literal_hit_count(matched: list[str], literals: list[str]) -> int:
    matched_set = {str(item).lower() for item in matched}
    return sum(1 for item in literals if str(item).lower() in matched_set)


def _event_text(event: Event) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return " ".join(
        part
        for part in [
            event.summary,
            event.action,
            event.causality,
            safe_json_dumps(payload) if payload else "",
        ]
        if str(part or "").strip()
    )


def _context_text(context: Context) -> str:
    facts = context.facts if isinstance(context.facts, dict) else {}
    facts_text = " ".join(f"{key} {value}" for key, value in facts.items())
    return " ".join(
        part
        for part in [
            context.subject,
            context.condition,
            facts_text,
            context.applies_when,
            context.summary,
            context.description,
        ]
        if str(part or "").strip()
    )


def _entity_text(entity: Entity) -> str:
    aliases = " ".join(str(item) for item in (entity.aliases or []))
    return " ".join(
        part
        for part in [entity.id, aliases, entity.type, entity.description]
        if str(part or "").strip()
    )


def _pattern_section_text(node: Any) -> str:
    if isinstance(node, dict):
        section = str(node.get("section", "") or "")
        return _plain_markdown(section)
    return _plain_markdown(str(node or ""))


def _context_card_text(node: Any) -> str:
    if not isinstance(node, Context):
        return ""
    condition = node.condition or node.summary or node.description
    parts = [str(condition or "").strip()]
    facts = node.facts if isinstance(node.facts, dict) else {}
    if facts:
        facts_text = "，".join(f"{key}{value}" for key, value in list(facts.items())[:4])
        parts.append(facts_text)
    if node.applies_when:
        parts.append(f"适用于{node.applies_when}")
    return _join_clause(*parts)


def _event_card_text(event: Event) -> str:
    return _join_clause(event.summary, event.action, event.causality)


def _join_clause(*parts: str) -> str:
    out: list[str] = []
    for part in parts:
        text = re.sub(r"\s+", " ", str(part or "")).strip(" ，,。；;")
        if text and text not in out:
            out.append(text)
    return _trim_line("；".join(out))


def _trim_line(text: str, limit: int = 160) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip(" ，,。；;")
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(1, limit - 1)].rstrip(" ，,。；;") + "…"


def _plain_markdown(text: str) -> str:
    lines = []
    for line in str(text or "").splitlines():
        clean = line.strip()
        clean = re.sub(r"^#{1,6}\s*", "", clean)
        clean = re.sub(r"^[-*]\s*", "", clean)
        if clean:
            lines.append(clean)
    return _join_clause(*lines)


def _display_kind(memory_type: str) -> str:
    return {"rule": "Rule", "context": "Context", "event": "Event"}.get(memory_type, "Event")


def _normalize_key(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").lower())


def _unique(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = str(value or "").lower()
        if key and key not in seen:
            out.append(str(value))
            seen.add(key)
    return out


def _clone_path(path: MemoryPath) -> MemoryPath:
    return MemoryPath(
        path_type=path.path_type,
        memory_type=path.memory_type,
        anchor_terms=list(path.anchor_terms),
        nodes=list(path.nodes),
        relations=[dict(item) for item in path.relations],
        path_length=path.path_length,
        source=path.source,
        direct_literal_hits=path.direct_literal_hits,
        direct_entity_hits=path.direct_entity_hits,
        support_count=path.support_count,
        last_active=path.last_active,
        folded_text=path.folded_text,
        folded_reason=path.folded_reason,
        warning=path.warning,
    )


def _path_rank_tuple(path: MemoryPath) -> tuple[int, int, int, int]:
    return (
        int(path.direct_literal_hits or 0),
        int(path.direct_entity_hits or 0),
        int(path.support_count or 1),
        int(path.last_active or 0),
    )
