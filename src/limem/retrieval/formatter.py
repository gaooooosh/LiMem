"""Format diagnostic retrieval paths as compact Markdown for an LLM Agent."""

from limem.memory_store import MemoryStore

from .evidence_selector import select_evidence
from .models import RetrievalResult

_STATUS_PREFIX = {
    "active": "",
    "candidate": "[候选] ",
    "inactive": "[已失效] ",
}


class RetrievalResultFormatter:
    """Project RetrievalResults into deterministic, human-readable Markdown."""

    def __init__(self, memory_store: MemoryStore) -> None:
        self._store = memory_store

    def format(
        self, results: list[RetrievalResult], *, query: str | None, context: str | None
    ) -> str:
        """Return ranked, self-contained memory groups without diagnostics."""

        if not results:
            return "未检索到相关记忆。"

        groups = []
        for rank, result in enumerate(results, 1):
            lines = [f"### 相关记忆[排名{rank}]"]
            for step in result.steps:
                item = step.memory_item
                statement = item.statement
                if item.condition is not None:
                    statement += f"（适用条件：{item.condition}）"
                lines.append(f"- {_STATUS_PREFIX[item.status]}{statement}")
                evidence = select_evidence(
                    self._store.get_direct_evidence(item.id),
                    query=query,
                    context=context,
                )
                for source in evidence:
                    lines.extend([
                        "",
                        f"#### 历史发生背景 · Evidence {source.id}",
                        f"来源 Session：{source.source_session_id}",
                        f"来源 Raw Event：{', '.join(source.raw_event_ids)}",
                        "",
                        source.context,
                    ])
            groups.append("\n".join(lines))
        return "\n\n".join(groups)
