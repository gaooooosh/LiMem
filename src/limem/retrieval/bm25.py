"""Pure Python Okapi BM25 retrieval for LiMem events."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

import jieba

from limem.core.event import Event


_ENGLISH_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


@dataclass
class _Document:
    event: Event
    tokens: list[str]
    term_freq: Counter[str]


class BM25Index:
    """In-memory Okapi BM25 index over active LiMem events."""

    def __init__(self, events: Iterable[Event] | None = None, *, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._documents: dict[str, _Document] = {}
        self._document_frequency: Counter[str] = Counter()
        self._total_document_length = 0
        self._average_document_length = 0.0
        if events is not None:
            self.rebuild(list(events))

    @property
    def size(self) -> int:
        return len(self._documents)

    @property
    def avgdl(self) -> float:
        return self._average_document_length

    def rebuild(self, events: list[Event]) -> None:
        """Clear the index and rebuild it from events."""
        self._documents.clear()
        self._document_frequency.clear()
        self._total_document_length = 0
        self._average_document_length = 0.0
        for event in events:
            self.add_event(event)

    def add_event(self, event: Event) -> None:
        """Add or replace a single event in the index."""
        if not event.id:
            return
        if event.id in self._documents:
            self.remove_event(event.id)

        tokens = tokenize(_event_text(event))
        term_freq = Counter(tokens)
        self._documents[event.id] = _Document(event=event, tokens=tokens, term_freq=term_freq)
        self._total_document_length += len(tokens)
        self._document_frequency.update(term_freq.keys())
        self._recompute_average_document_length()

    def remove_event(self, event_id: str) -> None:
        """Remove an event from the index if present."""
        document = self._documents.pop(event_id, None)
        if document is None:
            return
        self._total_document_length -= len(document.tokens)
        for token in document.term_freq:
            self._document_frequency[token] -= 1
            if self._document_frequency[token] <= 0:
                del self._document_frequency[token]
        self._recompute_average_document_length()

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """Search events and return normalized result dictionaries."""
        if not query or top_k <= 0 or not self._documents:
            return []

        query_terms = tokenize(query)
        if not query_terms:
            return []

        scored: list[tuple[float, Event]] = []
        for document in self._documents.values():
            score = self._score_document(query_terms, document)
            if score > 0.0:
                scored.append((score, document.event))

        scored.sort(key=lambda item: (item[0], item[1].last_active or item[1].timestamp), reverse=True)
        return [self._format_result(event, score) for score, event in scored[:top_k]]

    def _score_document(self, query_terms: list[str], document: _Document) -> float:
        document_count = len(self._documents)
        document_length = len(document.tokens)
        if document_count == 0 or document_length == 0:
            return 0.0

        score = 0.0
        for term in query_terms:
            frequency = document.term_freq.get(term, 0)
            if frequency == 0:
                continue
            document_frequency = self._document_frequency.get(term, 0)
            idf = math.log(1.0 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5))
            length_norm = 1.0 - self.b + self.b * document_length / (self._average_document_length or 1.0)
            score += idf * (frequency * (self.k1 + 1.0)) / (frequency + self.k1 * length_norm)
        return score

    def _format_result(self, event: Event, score: float) -> dict:
        return {
            "event_id": event.id,
            "summary": event.summary,
            "action": event.action,
            "causality": event.causality,
            "timestamp": event.timestamp,
            "score": score,
        }

    def _recompute_average_document_length(self) -> None:
        self._average_document_length = (
            self._total_document_length / len(self._documents) if self._documents else 0.0
        )


def tokenize(text: str) -> list[str]:
    """Tokenize Chinese with jieba search mode and English by regex."""
    tokens: list[str] = []
    for raw_token in jieba.cut_for_search(text or ""):
        token = raw_token.strip()
        if not token:
            continue
        matches = list(_ENGLISH_TOKEN_RE.finditer(token))
        if matches and "".join(match.group(0) for match in matches) == token:
            tokens.extend(match.group(0).lower() for match in matches)
        else:
            tokens.append(token)
            tokens.extend(match.group(0).lower() for match in matches)
    return tokens


def _event_text(event: Event) -> str:
    return " ".join(part for part in [event.summary, event.action, event.causality] if part)
