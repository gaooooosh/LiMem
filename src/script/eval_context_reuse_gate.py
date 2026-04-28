# -*- coding: utf-8 -*-
"""Evaluate the lightweight Context reuse gate on a JSONL benchmark."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from statistics import fmean
from typing import Any

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from limem.core.context import Context, ContextDraft
from limem.core.event import Event
from limem.evolution.dynamic_engine import DynamicEvolutionConfig, DynamicEvolutionEngine


DEFAULT_DATASET_PATH = os.path.join(PROJECT_ROOT, "tests", "fixtures", "context_reuse_benchmark.jsonl")


@dataclass
class EvalResult:
    case_id: str
    category: str
    expected_reuse: bool
    expected_context_id: str
    predicted_reuse: bool
    predicted_context_id: str
    correct: bool
    latency_ms: float
    candidate_count: int


class _BenchmarkStore:
    def __init__(self, contexts: list[Context]):
        self.contexts = list(contexts)
        self.context_by_id = {context.id: context for context in self.contexts}
        self.find_context_candidates_calls = 0
        self.find_contexts_summary_index_calls = 0
        self.get_context_calls = 0

    def find_context_candidates(
        self,
        context_type: str,
        subtype: str = "",
        limit: int = 20,
        only_active: bool = True,
    ) -> list[Context]:
        del context_type
        self.find_context_candidates_calls += 1
        candidates = self.contexts
        if only_active:
            candidates = [context for context in candidates if context.status == "active"]
        if subtype:
            candidates = [context for context in candidates if context.subtype == subtype]
        return list(candidates)[: max(1, int(limit or 1))]

    def find_contexts_summary_index(self, context_type: str, only_active: bool = True) -> list[tuple[str, str]]:
        del context_type
        self.find_contexts_summary_index_calls += 1
        contexts = self.contexts
        if only_active:
            contexts = [context for context in contexts if context.status == "active"]
        return [(context.id, context.summary) for context in contexts]

    def get_context(self, context_id: str) -> Context | None:
        self.get_context_calls += 1
        return self.context_by_id.get(context_id)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate lightweight Context reuse gate")
    parser.add_argument("--dataset", default=DEFAULT_DATASET_PATH, help="Benchmark JSONL path")
    parser.add_argument("--output", default="", help="Optional JSON report output path")
    parser.add_argument("--score-threshold", type=float, default=None, help="Override context_reuse_score_threshold")
    parser.add_argument("--summary-overlap", type=float, default=None, help="Override context_reuse_min_summary_overlap")
    parser.add_argument("--evidence-overlap", type=float, default=None, help="Override context_reuse_min_evidence_overlap")
    parser.add_argument("--candidate-limit", type=int, default=None, help="Override context_reuse_candidate_limit")
    parser.add_argument("--disable-gate", action="store_true", help="Disable lightweight reuse gate for baseline comparison")
    parser.add_argument("--allow-cross-subtype", action="store_true", help="Allow fuzzy cross-subtype reuse")
    return parser.parse_args()


def _load_jsonl(path: str) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as file:
        for line_no, line in enumerate(file, start=1):
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            try:
                cases.append(json.loads(text))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
    return cases


def _make_config(args: argparse.Namespace) -> DynamicEvolutionConfig:
    config = DynamicEvolutionConfig(
        context_reuse_gating_enabled=not args.disable_gate,
        context_reuse_allow_cross_subtype=bool(args.allow_cross_subtype),
        llm_api_key="",
    )
    if args.score_threshold is not None:
        config.context_reuse_score_threshold = args.score_threshold
    if args.summary_overlap is not None:
        config.context_reuse_min_summary_overlap = args.summary_overlap
    if args.evidence_overlap is not None:
        config.context_reuse_min_evidence_overlap = args.evidence_overlap
    if args.candidate_limit is not None:
        config.context_reuse_candidate_limit = args.candidate_limit
    config.__post_init__()
    return config


def _make_context(data: dict[str, Any]) -> Context:
    return Context(
        id=str(data.get("id", "") or ""),
        context_type=str(data.get("context_type", "context") or "context"),
        subtype=str(data.get("subtype", "situation") or "situation"),
        summary=str(data.get("summary", "") or ""),
        description=str(data.get("description", "") or ""),
        confidence=float(data.get("confidence", 0.8) or 0.8),
        support_count=int(data.get("support_count", 1) or 1),
        created_at=int(data.get("created_at", 0) or 0),
        updated_at=int(data.get("updated_at", 0) or 0),
        valid_from=int(data.get("valid_from", 0) or 0),
        valid_to=data.get("valid_to"),
        last_seen_at=int(data.get("last_seen_at", data.get("updated_at", 0)) or 0),
        status=str(data.get("status", "active") or "active"),
        source_refs=list(data.get("source_refs", []) or []),
    )


def _make_event(data: dict[str, Any]) -> Event:
    return Event(
        id=str(data.get("id", "") or ""),
        summary=str(data.get("summary", "") or ""),
        action=str(data.get("action", "") or ""),
        causality=str(data.get("causality", "") or ""),
        timestamp=int(data.get("timestamp", 0) or 0),
        last_active=int(data.get("last_active", 0) or 0),
        valid_from=int(data.get("valid_from", 0) or 0),
        valid_to=data.get("valid_to"),
        participants=list(data.get("participants", []) or []),
        payload=dict(data.get("payload", {}) or {}),
        evidence=list(data.get("evidence", []) or []),
        status=str(data.get("status", "active") or "active"),
    )


def _make_draft(data: dict[str, Any]) -> ContextDraft:
    return ContextDraft(
        subtype=str(data.get("subtype", "situation") or "situation"),
        summary=str(data.get("summary", "") or ""),
        description=str(data.get("description", "") or ""),
        confidence=float(data.get("confidence", 0.8) or 0.8),
        evidence_span=str(data.get("evidence_span", "") or ""),
        source_refs=list(data.get("source_refs", []) or []),
        valid_from=int(data.get("valid_from", 0) or 0),
        valid_to=data.get("valid_to"),
    )


def _evaluate_case(case: dict[str, Any], config: DynamicEvolutionConfig) -> EvalResult:
    contexts = [_make_context(item) for item in case.get("existing_contexts", [])]
    store = _BenchmarkStore(contexts)
    engine = DynamicEvolutionEngine(store=store, config=config)
    engine._maybe_embed_context = lambda text: None

    event = _make_event(case.get("event", {}))
    draft = _make_draft(case.get("draft", {}))
    expected = case.get("expected", {})
    expected_reuse = bool(expected.get("should_reuse", False))
    expected_context_id = str(expected.get("expected_context_id", "") or "")

    start = time.perf_counter()
    match = engine.match_existing_context(draft, event=event)
    latency_ms = (time.perf_counter() - start) * 1000.0

    predicted_context_id = match.id if match is not None else ""
    predicted_reuse = match is not None
    correct = predicted_reuse == expected_reuse and (
        not expected_reuse or predicted_context_id == expected_context_id
    )
    return EvalResult(
        case_id=str(case.get("case_id", "") or ""),
        category=str(case.get("category", "uncategorized") or "uncategorized"),
        expected_reuse=expected_reuse,
        expected_context_id=expected_context_id,
        predicted_reuse=predicted_reuse,
        predicted_context_id=predicted_context_id,
        correct=correct,
        latency_ms=latency_ms,
        candidate_count=len(contexts),
    )


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def _summarize(results: list[EvalResult], *, categories: list[str]) -> dict[str, Any]:
    total = len(results)
    expected_reuse = [item for item in results if item.expected_reuse]
    expected_new = [item for item in results if not item.expected_reuse]
    predicted_reuse = [item for item in results if item.predicted_reuse]
    correct_reuse = [
        item for item in results
        if item.expected_reuse and item.predicted_context_id == item.expected_context_id
    ]
    wrong_reuse = [item for item in results if item.predicted_reuse and not item.expected_reuse]
    correct_new = [item for item in results if not item.expected_reuse and not item.predicted_reuse]
    latencies = [item.latency_ms for item in results]
    summary = {
        "cases": total,
        "accuracy": _safe_div(sum(1 for item in results if item.correct), total),
        "reuse_precision": _safe_div(len(correct_reuse), len(predicted_reuse)),
        "reuse_recall": _safe_div(len(correct_reuse), len(expected_reuse)),
        "false_link_rate": _safe_div(len(wrong_reuse), total),
        "new_context_safety_rate": _safe_div(len(correct_new), len(expected_new)),
        "predicted_reuse_rate": _safe_div(len(predicted_reuse), total),
        "expected_reuse_rate": _safe_div(len(expected_reuse), total),
        "avg_candidate_count": fmean([item.candidate_count for item in results]) if results else 0.0,
        "avg_latency_ms": fmean(latencies) if latencies else 0.0,
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "llm_calls": 0,
        "embedding_calls": 0,
    }
    distractor_categories = {
        "related_but_ungrounded",
        "same_scene_different_intent",
        "negation_conflict",
        "cross_subtype_trap",
        "temporal_conflict",
        "weak_or_missing_evidence",
    }
    distractors = [item for item in results if item.category in distractor_categories]
    summary["distractor_false_reuse_rate"] = _safe_div(
        sum(1 for item in distractors if item.predicted_reuse),
        len(distractors),
    )
    by_category = {}
    for category in categories:
        subset = [item for item in results if item.category == category]
        by_category[category] = {
            "cases": len(subset),
            "accuracy": _safe_div(sum(1 for item in subset if item.correct), len(subset)),
            "false_link_rate": _safe_div(
                sum(1 for item in subset if item.predicted_reuse and not item.expected_reuse),
                len(subset),
            ),
            "reuse_recall": _safe_div(
                sum(1 for item in subset if item.expected_reuse and item.predicted_context_id == item.expected_context_id),
                sum(1 for item in subset if item.expected_reuse),
            ),
        }
    return {"overall": summary, "by_category": by_category}


def _result_to_dict(result: EvalResult) -> dict[str, Any]:
    return {
        "case_id": result.case_id,
        "category": result.category,
        "expected_reuse": result.expected_reuse,
        "expected_context_id": result.expected_context_id,
        "predicted_reuse": result.predicted_reuse,
        "predicted_context_id": result.predicted_context_id,
        "correct": result.correct,
        "latency_ms": round(result.latency_ms, 4),
        "candidate_count": result.candidate_count,
    }


def _print_report(report: dict[str, Any]) -> None:
    overall = report["metrics"]["overall"]
    print("Context reuse gate benchmark")
    print(f"Dataset: {report['dataset']}")
    print(f"Cases: {overall['cases']}")
    print(
        "Overall: "
        f"accuracy={overall['accuracy']:.3f}, "
        f"precision={overall['reuse_precision']:.3f}, "
        f"recall={overall['reuse_recall']:.3f}, "
        f"false_link={overall['false_link_rate']:.3f}, "
        f"distractor_false_reuse={overall['distractor_false_reuse_rate']:.3f}"
    )
    print(
        "Latency: "
        f"avg={overall['avg_latency_ms']:.3f}ms, "
        f"p50={overall['p50_latency_ms']:.3f}ms, "
        f"p95={overall['p95_latency_ms']:.3f}ms, "
        f"llm_calls={overall['llm_calls']}, embedding_calls={overall['embedding_calls']}"
    )
    print("\nBy category:")
    for category, metrics in report["metrics"]["by_category"].items():
        print(
            f"- {category}: cases={metrics['cases']}, "
            f"accuracy={metrics['accuracy']:.3f}, "
            f"false_link={metrics['false_link_rate']:.3f}, "
            f"reuse_recall={metrics['reuse_recall']:.3f}"
        )

    failures = [item for item in report["results"] if not item["correct"]]
    if failures:
        print("\nFailures:")
        for item in failures:
            print(
                f"- {item['case_id']}: expected={item['expected_context_id'] or 'NEW'}, "
                f"predicted={item['predicted_context_id'] or 'NEW'}"
            )


def main() -> int:
    args = _parse_args()
    cases = _load_jsonl(args.dataset)
    config = _make_config(args)
    categories = sorted({str(case.get("category", "uncategorized")) for case in cases})
    results = [_evaluate_case(case, config) for case in cases]
    report = {
        "dataset": os.path.abspath(args.dataset),
        "config": {
            "context_reuse_gating_enabled": config.context_reuse_gating_enabled,
            "context_reuse_candidate_limit": config.context_reuse_candidate_limit,
            "context_reuse_score_threshold": config.context_reuse_score_threshold,
            "context_reuse_min_summary_overlap": config.context_reuse_min_summary_overlap,
            "context_reuse_min_evidence_overlap": config.context_reuse_min_evidence_overlap,
            "context_reuse_require_evidence": config.context_reuse_require_evidence,
            "context_reuse_allow_cross_subtype": config.context_reuse_allow_cross_subtype,
        },
        "metrics": _summarize(results, categories=categories),
        "results": [_result_to_dict(result) for result in results],
    }
    _print_report(report)
    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as file:
            json.dump(report, file, ensure_ascii=False, indent=2)
        print(f"\nWrote report: {args.output}")
    return 0 if all(result.correct for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
