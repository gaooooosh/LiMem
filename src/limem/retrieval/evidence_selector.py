"""Select whole Direct Evidence by query-first BM25 without model calls."""

import sqlite3

from limem.memory_store import Evidence
from limem.memory_store.store import _tokens


def select_evidence(
    evidence: list[Evidence], *, query: str | None, context: str | None
) -> list[Evidence]:
    """Return at most three occurrences; query score precedes context score."""
    db = sqlite3.connect(":memory:")
    try:
        db.execute("CREATE VIRTUAL TABLE evidence_fts USING fts5(evidence_id UNINDEXED, text)")
        db.executemany(
            "INSERT INTO evidence_fts VALUES (?, ?)",
            [(e.id, " ".join(_tokens(e.context))) for e in evidence],
        )

        def scores(text: str | None) -> dict[str, float]:
            values = dict.fromkeys((e.id for e in evidence), 0.0)
            if text is not None:
                terms = dict.fromkeys(_tokens(text))
                match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
                for eid, score in db.execute(
                    "SELECT evidence_id, bm25(evidence_fts) FROM evidence_fts WHERE evidence_fts MATCH ?",
                    (match,),
                ):
                    values[eid] = -score
            return values

        query_scores = scores(query)
        context_scores = scores(context)
        return sorted(
            evidence,
            key=lambda e: (-query_scores[e.id], -context_scores[e.id], e.id),
        )[:3]
    finally:
        db.close()
