# -*- coding: utf-8 -*-
"""Benchmark adaptive extraction quality, routing and latency on trips.json."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from statistics import fmean
from typing import Any, Callable

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from limem.builder.extractor import AdaptiveExtractor, ExtractionResult, TwoStageExtractor
from limem.builder.input_classifier import InputClassifier, StructureLevel
from limem.builder.relationship_inferrer import RelationshipInferrer
from limem.config import DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL, ENABLE_THINKING, GENERATION_MODEL
from limem.core.event import Event
from limem.utils import _normalize_entity_name, load_prompt, robust_json_loads
from script.trips_loader import load_trips_episodes


NON_NOISE_BUCKETS = (
    "车机对话数据",
    "媒体播放数据",
    "导航记录数据",
    "用户私人日程数据",
)
ROUTE_NAMES = tuple(level.name for level in StructureLevel)


@dataclass
class BenchmarkRun:
    name: str
    records: list[dict[str, Any]]
    empty_episodes: list[dict[str, Any]]
    metrics: dict[str, Any]


@dataclass
class LLMCallTracker:
    runner: Callable[[str, str, Any], Any]
    prompt_aliases: dict[str, str] = field(default_factory=dict)
    total_calls: int = 0
    current_calls: int = 0
    prompt_counts: Counter[str] = field(default_factory=Counter)

    def start_episode(self) -> None:
        self.current_calls = 0

    def finish_episode(self) -> int:
        calls = self.current_calls
        self.current_calls = 0
        return calls

    def call(self, system_prompt: str, user_message: str, default: Any) -> Any:
        self.total_calls += 1
        self.current_calls += 1
        alias = self.prompt_aliases.get(system_prompt, "unknown")
        self.prompt_counts[alias] += 1
        return self.runner(system_prompt, user_message, default)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark adaptive extraction on trips.json")
    parser.add_argument(
        "--trips-path",
        default=os.path.join(PROJECT_ROOT, "trips.json"),
        help="Path to trips.json",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=0,
        help="Max episodes to benchmark (0 means all)",
    )
    parser.add_argument(
        "--buckets",
        default="",
        help="Comma-separated bucket names to include (empty means all)",
    )
    parser.add_argument(
        "--no-sort",
        action="store_true",
        help="Keep original flattened order instead of sorting by timestamp.",
    )
    parser.add_argument(
        "--online",
        action="store_true",
        help="Use real DashScope LLM calls instead of mock responses.",
    )
    parser.add_argument(
        "--skip-baseline",
        action="store_true",
        help="Skip the TwoStageExtractor baseline benchmark.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=100,
        help="Print progress every N episodes.",
    )
    parser.add_argument(
        "--output-dir",
        default=os.path.join(PROJECT_ROOT, "outputs"),
        help="Directory for benchmark outputs.",
    )
    return parser.parse_args()


def _safe_div(numerator: float, denominator: float) -> float:
    if not denominator:
        return 0.0
    return numerator / denominator


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    lower = ordered[lower_index]
    upper = ordered[upper_index]
    weight = position - lower_index
    return lower + (upper - lower) * weight


def _serialize_extraction_result(result: ExtractionResult) -> dict[str, Any]:
    return {
        "event_data": result.event_data,
        "events_data": result.events_data,
        "entities": result.entities,
        "confidence": result.confidence,
    }


def _count_non_empty_summaries(events_data: list[dict[str, Any]]) -> int:
    return sum(1 for item in events_data if str(item.get("summary", "") or "").strip())


def _count_valid_entities(entities: list[str]) -> int:
    return sum(1 for item in entities if _normalize_entity_name(item))


def _event_from_payload(
    payload: dict[str, Any],
    episode_timestamp: int,
    episode_index: int,
    event_index: int,
) -> Event:
    time_range = payload.get("time_range", {})
    if not isinstance(time_range, dict):
        time_range = {}
    participants = payload.get("participants", [])
    if not isinstance(participants, list):
        participants = []
    return Event(
        id=f"ep{episode_index:04d}_ev{event_index:02d}",
        summary=str(payload.get("summary", "") or ""),
        action=str(payload.get("action", "") or ""),
        causality=str(payload.get("causality", "") or ""),
        time_range=time_range,
        timestamp=int(episode_timestamp or 0),
        last_active=int(episode_timestamp or 0),
        participants=participants,
        payload=payload,
    )


def _infer_relation_counts(
    inferrer: RelationshipInferrer,
    events_data: list[dict[str, Any]],
    episode_timestamp: int,
    episode_index: int,
) -> dict[str, int]:
    events = [
        _event_from_payload(item, episode_timestamp=episode_timestamp, episode_index=episode_index, event_index=index)
        for index, item in enumerate(events_data, 1)
        if isinstance(item, dict)
    ]
    relations = inferrer.infer(events)
    counts = Counter(item.relation_type for item in relations)
    return {
        "temporal_next": counts.get("temporal_next", 0),
        "causality": counts.get("causality", 0),
        "parallel": counts.get("parallel", 0),
        "total": len(relations),
    }


def _aggregate_group(
    records: list[dict[str, Any]],
    *,
    denominator: int | None = None,
    include_route_distribution: bool = False,
) -> dict[str, Any]:
    count = len(records)
    total_events = sum(record["event_count"] for record in records)
    total_entities = sum(record["entity_count"] for record in records)
    valid_entities = sum(record["valid_entity_count"] for record in records)
    summary_non_empty = sum(record["summary_non_empty_count"] for record in records)
    empty_count = sum(1 for record in records if record["empty"])
    llm_call_count = sum(record["llm_calls"] for record in records)
    llm_episode_count = sum(1 for record in records if record["llm_calls"] > 0)
    errors = sum(1 for record in records if record["error"])
    latencies = [float(record["latency_ms"]) for record in records]
    relation_total = sum(record["relation_counts"]["total"] for record in records)

    metrics = {
        "count": count,
        "rate": _safe_div(count, denominator) if denominator is not None else 0.0,
        "events": total_events,
        "event_yield": _safe_div(total_events, count),
        "entities": total_entities,
        "entity_yield": _safe_div(total_entities, count),
        "entity_precision": _safe_div(valid_entities, total_entities),
        "summary_quality": _safe_div(summary_non_empty, total_events),
        "empty": empty_count,
        "empty_rate": _safe_div(empty_count, count),
        "llm_call_count": llm_call_count,
        "llm_call_rate": _safe_div(llm_call_count, count),
        "llm_episode_count": llm_episode_count,
        "llm_episode_rate": _safe_div(llm_episode_count, count),
        "avg_latency_ms": fmean(latencies) if latencies else 0.0,
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "p99_latency_ms": _percentile(latencies, 0.99),
        "errors": errors,
        "relation_total": relation_total,
        "relation_per_event": _safe_div(relation_total, total_events),
    }
    if include_route_distribution:
        metrics["route_distribution"] = _make_route_distribution(records, total=count)
    return metrics


def _make_route_distribution(records: list[dict[str, Any]], *, total: int) -> dict[str, dict[str, float]]:
    route_counts = Counter(record["route"] for record in records)
    return {
        route: {
            "count": route_counts.get(route, 0),
            "rate": _safe_div(route_counts.get(route, 0), total),
        }
        for route in ROUTE_NAMES
    }


def compute_metrics(
    records: list[dict[str, Any]],
    *,
    extractor_name: str,
    wall_time_ms: float,
    llm_prompt_counts: Counter[str] | None = None,
) -> dict[str, Any]:
    total_episodes = len(records)
    overall = _aggregate_group(records, denominator=total_episodes)
    latencies = [float(record["latency_ms"]) for record in records]
    by_route = {
        route: _aggregate_group(
            [record for record in records if record["route"] == route],
            denominator=total_episodes,
        )
        for route in ROUTE_NAMES
    }

    bucket_names = sorted(
        {str(record["bucket_name"]) for record in records},
        key=lambda name: (
            -sum(1 for record in records if record["bucket_name"] == name),
            name,
        ),
    )
    by_bucket = {
        bucket_name: _aggregate_group(
            [record for record in records if record["bucket_name"] == bucket_name],
            denominator=total_episodes,
            include_route_distribution=True,
        )
        for bucket_name in bucket_names
    }

    relation_counts = {
        "temporal_next": sum(record["relation_counts"]["temporal_next"] for record in records),
        "causality": sum(record["relation_counts"]["causality"] for record in records),
        "parallel": sum(record["relation_counts"]["parallel"] for record in records),
    }
    relation_counts["total"] = (
        relation_counts["temporal_next"] + relation_counts["causality"] + relation_counts["parallel"]
    )
    relation_counts["per_event"] = _safe_div(relation_counts["total"], overall["events"])

    metrics = {
        "extractor": extractor_name,
        "total_episodes": total_episodes,
        "total_events": overall["events"],
        "total_entities": overall["entities"],
        "empty_count": overall["empty"],
        "empty_rate": overall["empty_rate"],
        "event_yield": overall["event_yield"],
        "summary_quality": overall["summary_quality"],
        "entity_yield": overall["entity_yield"],
        "entity_precision": overall["entity_precision"],
        "llm_call_count": overall["llm_call_count"],
        "llm_call_rate": overall["llm_call_rate"],
        "llm_episode_count": overall["llm_episode_count"],
        "llm_episode_rate": overall["llm_episode_rate"],
        "total_latency_ms": sum(latencies),
        "avg_latency_ms": overall["avg_latency_ms"],
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "p99_latency_ms": _percentile(latencies, 0.99),
        "benchmark_wall_time_ms": wall_time_ms,
        "error_count": overall["errors"],
        "route_distribution": _make_route_distribution(records, total=total_episodes),
        "by_route": by_route,
        "by_bucket": by_bucket,
        "relations": relation_counts,
        "llm_call_breakdown": dict(sorted((llm_prompt_counts or Counter()).items())),
    }
    metrics["validation"] = _build_validation(metrics)
    return metrics


def _build_validation(metrics: dict[str, Any]) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "empty_rate_lt_0.40": {
            "passed": metrics["empty_rate"] < 0.40,
            "actual": metrics["empty_rate"],
            "threshold": 0.40,
        },
        "llm_call_rate_lt_0.15": {
            "passed": metrics["llm_call_rate"] < 0.15,
            "actual": metrics["llm_call_rate"],
            "threshold": 0.15,
        },
        "structured_avg_latency_ms_lt_5": {
            "passed": metrics["by_route"]["STRUCTURED"]["avg_latency_ms"] < 5.0,
            "actual": metrics["by_route"]["STRUCTURED"]["avg_latency_ms"],
            "threshold": 5.0,
        },
        "temporal_edges_gt_0": {
            "passed": metrics["relations"]["temporal_next"] > 0,
            "actual": metrics["relations"]["temporal_next"],
            "threshold": 0,
        },
    }

    non_noise = {}
    for bucket_name in NON_NOISE_BUCKETS:
        if bucket_name not in metrics["by_bucket"]:
            continue
        event_yield = metrics["by_bucket"][bucket_name]["event_yield"]
        non_noise[bucket_name] = {
            "passed": event_yield > 0.80,
            "actual": event_yield,
            "threshold": 0.80,
        }

    all_passed = all(item["passed"] for item in checks.values()) and all(
        item["passed"] for item in non_noise.values()
    )
    return {
        "checks": checks,
        "non_noise_bucket_event_yield_gt_0.80": non_noise,
        "all_passed": all_passed,
    }


def _compute_comparison(
    adaptive_metrics: dict[str, Any],
    baseline_metrics: dict[str, Any] | None,
) -> dict[str, Any]:
    if not baseline_metrics:
        return {}

    baseline_llm_calls = baseline_metrics.get("llm_call_count", 0)
    adaptive_total_latency = adaptive_metrics.get("total_latency_ms", 0.0)
    baseline_total_latency = baseline_metrics.get("total_latency_ms", 0.0)
    return {
        "event_count_delta": adaptive_metrics.get("total_events", 0) - baseline_metrics.get("total_events", 0),
        "llm_savings": (
            None
            if baseline_llm_calls <= 0
            else 1.0 - (adaptive_metrics.get("llm_call_count", 0) / baseline_llm_calls)
        ),
        "latency_speedup": (
            None if adaptive_total_latency <= 0 else baseline_total_latency / adaptive_total_latency
        ),
    }


def _mock_llm_runner(system_prompt: str, user_message: str, default: Any) -> Any:
    del system_prompt, user_message
    return default


def _build_online_runner() -> Callable[[str, str, Any], Any]:
    try:
        from limem.llm import DashScopeClient
    except Exception as exc:  # pragma: no cover - depends on local runtime
        raise RuntimeError("dashscope is required for --online benchmark mode.") from exc

    if not DASHSCOPE_API_KEY or DASHSCOPE_API_KEY in {"YOUR_API_KEY", "sk-xxx"}:
        raise RuntimeError("Set DASHSCOPE_API_KEY before running --online benchmark mode.")
    client = DashScopeClient(
        api_key=DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_BASE_URL,
    )

    def _runner(system_prompt: str, user_message: str, default: Any) -> Any:
        resp = client.call_generation(
            model=GENERATION_MODEL,
            messages=client.build_messages(system_prompt, user_message),
            result_format="message",
            enable_thinking=ENABLE_THINKING,
        )
        if not client.is_success(resp):
            raise RuntimeError(
                f"LLM call failed: {client.error_summary(resp)}"
            )
        content = client.message_content(resp)
        return robust_json_loads(content, default)

    return _runner


def _build_two_stage_extractor(tracker: LLMCallTracker) -> TwoStageExtractor:
    extractor = object.__new__(TwoStageExtractor)
    extractor.api_key = DASHSCOPE_API_KEY
    extractor.base_url = DASHSCOPE_BASE_URL
    extractor.generation_model = GENERATION_MODEL
    extractor.enable_thinking = ENABLE_THINKING
    extractor._event_segment_system_prompt = load_prompt("extract_event_segments_system.txt")
    extractor._event_segment_user_prompt = load_prompt("extract_event_segments_user.txt")
    extractor._event_struct_system_prompt = load_prompt("extract_event_struct_system.txt")
    extractor._event_struct_user_prompt = load_prompt("extract_event_struct_user.txt")
    extractor._event_system_prompt = load_prompt("extract_event_only_system.txt")
    extractor._event_user_prompt = load_prompt("extract_event_only_user.txt")
    extractor._entity_system_prompt = load_prompt("extract_entities_only_system.txt")
    extractor._entity_user_prompt = load_prompt("extract_entities_only_user.txt")
    extractor._call_generation_json = tracker.call
    tracker.prompt_aliases = {
        extractor._event_segment_system_prompt: "segment",
        extractor._event_struct_system_prompt: "struct",
        extractor._event_system_prompt: "single_pass_event",
        extractor._entity_system_prompt: "entity",
    }
    return extractor


def _benchmark_extractor(
    extractor_name: str,
    extractor: Any,
    episodes: list[Any],
    tracker: LLMCallTracker,
    *,
    progress_every: int,
) -> BenchmarkRun:
    classifier = InputClassifier()
    inferrer = RelationshipInferrer()
    records: list[dict[str, Any]] = []
    empty_episodes: list[dict[str, Any]] = []
    wall_start = time.perf_counter()

    for episode_index, episode in enumerate(episodes, 1):
        classification = classifier.classify(episode.content, episode.metadata)
        tracker.start_episode()
        start = time.perf_counter()
        error = ""
        try:
            result = extractor.extract(episode.content, episode.metadata)
        except Exception as exc:  # pragma: no cover - benchmark should keep running
            error = f"{type(exc).__name__}: {exc}"
            result = ExtractionResult(event_data={}, events_data=[], entities=[], confidence=0.0)
        latency_ms = (time.perf_counter() - start) * 1000.0
        llm_calls = tracker.finish_episode()
        relation_counts = _infer_relation_counts(
            inferrer=inferrer,
            events_data=result.events_data,
            episode_timestamp=episode.timestamp,
            episode_index=episode_index,
        )
        record = {
            "episode_index": episode_index,
            "bucket_name": str(episode.metadata.get("bucket_name", "") or ""),
            "source": str(episode.metadata.get("source", "") or ""),
            "route": classification.level.name,
            "detected_patterns": list(classification.detected_patterns),
            "route_score": classification.score,
            "latency_ms": latency_ms,
            "llm_calls": llm_calls,
            "event_count": len(result.events_data),
            "entity_count": len(result.entities),
            "summary_non_empty_count": _count_non_empty_summaries(result.events_data),
            "valid_entity_count": _count_valid_entities(result.entities),
            "relation_counts": relation_counts,
            "empty": len(result.events_data) == 0,
            "error": error,
        }
        records.append(record)

        if record["empty"]:
            empty_episodes.append(
                {
                    "episode_index": episode_index,
                    "bucket_name": record["bucket_name"],
                    "source": record["source"],
                    "route": record["route"],
                    "detected_patterns": record["detected_patterns"],
                    "route_score": record["route_score"],
                    "latency_ms": latency_ms,
                    "llm_calls": llm_calls,
                    "content": episode.content,
                    "metadata": episode.metadata,
                    "extraction_result": _serialize_extraction_result(result),
                    "reason": "extract_error" if error else "no_events_extracted",
                    "error": error,
                }
            )

        if progress_every > 0 and episode_index % progress_every == 0:
            print(f"[{extractor_name}] {episode_index}/{len(episodes)} episodes")

    wall_time_ms = (time.perf_counter() - wall_start) * 1000.0
    metrics = compute_metrics(
        records,
        extractor_name=extractor_name,
        wall_time_ms=wall_time_ms,
        llm_prompt_counts=tracker.prompt_counts,
    )
    return BenchmarkRun(
        name=extractor_name,
        records=records,
        empty_episodes=empty_episodes,
        metrics=metrics,
    )


def benchmark_offline(
    episodes: list[Any],
    *,
    progress_every: int,
    skip_baseline: bool,
) -> tuple[BenchmarkRun, BenchmarkRun | None]:
    """Mock LLM calls, benchmark routing, rule paths and latency."""

    adaptive_tracker = LLMCallTracker(runner=_mock_llm_runner)
    adaptive_extractor = AdaptiveExtractor(llm_caller=adaptive_tracker.call)
    adaptive_tracker.prompt_aliases = {
        adaptive_extractor._extract_combined_system_prompt: "combined",
    }
    adaptive_run = _benchmark_extractor(
        extractor_name="adaptive",
        extractor=adaptive_extractor,
        episodes=episodes,
        tracker=adaptive_tracker,
        progress_every=progress_every,
    )

    baseline_run = None
    if not skip_baseline:
        baseline_tracker = LLMCallTracker(runner=_mock_llm_runner)
        baseline_extractor = _build_two_stage_extractor(baseline_tracker)
        baseline_run = _benchmark_extractor(
            extractor_name="two_stage",
            extractor=baseline_extractor,
            episodes=episodes,
            tracker=baseline_tracker,
            progress_every=progress_every,
        )
    return adaptive_run, baseline_run


def benchmark_online(
    episodes: list[Any],
    *,
    progress_every: int,
    skip_baseline: bool,
) -> tuple[BenchmarkRun, BenchmarkRun | None]:
    """Use real DashScope calls to benchmark end-to-end extraction quality."""

    online_runner = _build_online_runner()

    adaptive_tracker = LLMCallTracker(runner=online_runner)
    adaptive_extractor = AdaptiveExtractor(llm_caller=adaptive_tracker.call)
    adaptive_tracker.prompt_aliases = {
        adaptive_extractor._extract_combined_system_prompt: "combined",
    }
    adaptive_run = _benchmark_extractor(
        extractor_name="adaptive",
        extractor=adaptive_extractor,
        episodes=episodes,
        tracker=adaptive_tracker,
        progress_every=progress_every,
    )

    baseline_run = None
    if not skip_baseline:
        baseline_tracker = LLMCallTracker(runner=online_runner)
        baseline_extractor = _build_two_stage_extractor(baseline_tracker)
        baseline_run = _benchmark_extractor(
            extractor_name="two_stage",
            extractor=baseline_extractor,
            episodes=episodes,
            tracker=baseline_tracker,
            progress_every=progress_every,
        )
    return adaptive_run, baseline_run


def _format_rate(value: float) -> str:
    return f"{value * 100:.1f}%"


def _format_ms(value: float) -> str:
    return f"{value:.2f}ms"


def print_report(report: dict[str, Any]) -> None:
    print("\n============ Extraction Benchmark Report ============")
    print(
        f"Mode: {report['mode']}    "
        f"Total Episodes: {report['total_episodes']}    "
        f"Total Events: {report['total_events']}    "
        f"Empty: {report['empty_count']} ({_format_rate(report['empty_rate'])})"
    )
    print(
        f"Event Yield: {report['event_yield']:.3f}    "
        f"Summary Quality: {_format_rate(report['summary_quality'])}    "
        f"Entity Yield: {report['entity_yield']:.3f}    "
        f"Entity Precision: {_format_rate(report['entity_precision'])}"
    )

    print("\n--- By Route ---")
    for route in ROUTE_NAMES:
        data = report["by_route"][route]
        print(
            f"{route:<16} | {data['count']:>3} eps | {data['events']:>4} events | "
            f"empty {data['empty']:>3} | avg {_format_ms(data['avg_latency_ms']):>8} | "
            f"p95 {_format_ms(data['p95_latency_ms']):>8} | llm {data['llm_call_count']:>3}"
        )

    print("\n--- By Bucket ---")
    for bucket_name, data in report["by_bucket"].items():
        print(
            f"{bucket_name:<16} | {data['count']:>3} eps | {data['events']:>4} events | "
            f"empty {data['empty']:>3} | avg {_format_ms(data['avg_latency_ms']):>8}"
        )

    print("\n--- Relations ---")
    relations = report["relations"]
    print(
        f"temporal_next: {relations['temporal_next']} | "
        f"causality: {relations['causality']} | "
        f"parallel: {relations['parallel']} | "
        f"per_event: {relations['per_event']:.3f}"
    )

    print(
        f"\nLLM calls: {report['llm_call_count']} "
        f"({_format_rate(report['llm_call_rate'])} of episodes)"
    )

    baseline = report.get("baseline")
    comparison = report.get("comparison", {})
    if baseline:
        llm_savings = comparison.get("llm_savings")
        latency_speedup = comparison.get("latency_speedup")
        llm_savings_text = "n/a" if llm_savings is None else _format_rate(llm_savings)
        latency_speedup_text = "n/a" if latency_speedup is None else f"{latency_speedup:.2f}x"
        print("\n--- Vs TwoStageExtractor ---")
        print(
            f"baseline events: {baseline['total_events']} | "
            f"event_count_delta: {comparison.get('event_count_delta', 0)} | "
            f"llm_savings: {llm_savings_text} | "
            f"latency_speedup: {latency_speedup_text}"
        )

    print("\n--- Validation ---")
    for name, item in report["validation"]["checks"].items():
        status = "PASS" if item["passed"] else "FAIL"
        print(f"{status:<4} {name} | actual={item['actual']:.4f} | threshold={item['threshold']}")
    for bucket_name, item in report["validation"]["non_noise_bucket_event_yield_gt_0.80"].items():
        status = "PASS" if item["passed"] else "FAIL"
        print(
            f"{status:<4} {bucket_name} event_yield>0.80 | "
            f"actual={item['actual']:.4f} | threshold={item['threshold']}"
        )

    print(f"\nReport: {report['report_path']}")
    print(f"Empty episodes: {report['empty_episodes_path']}")
    print("====================================================")


def _build_report(
    adaptive_run: BenchmarkRun,
    baseline_run: BenchmarkRun | None,
    *,
    mode: str,
    trips_path: str,
    output_dir: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    report = {
        "timestamp": timestamp,
        "mode": mode,
        "trips_path": trips_path,
        "generation_model": GENERATION_MODEL,
        "total_episodes": adaptive_run.metrics["total_episodes"],
        "total_events": adaptive_run.metrics["total_events"],
        "total_entities": adaptive_run.metrics["total_entities"],
        "empty_count": adaptive_run.metrics["empty_count"],
        "empty_rate": adaptive_run.metrics["empty_rate"],
        "event_yield": adaptive_run.metrics["event_yield"],
        "summary_quality": adaptive_run.metrics["summary_quality"],
        "entity_yield": adaptive_run.metrics["entity_yield"],
        "entity_precision": adaptive_run.metrics["entity_precision"],
        "llm_call_count": adaptive_run.metrics["llm_call_count"],
        "llm_call_rate": adaptive_run.metrics["llm_call_rate"],
        "llm_episode_count": adaptive_run.metrics["llm_episode_count"],
        "llm_episode_rate": adaptive_run.metrics["llm_episode_rate"],
        "total_latency_ms": adaptive_run.metrics["total_latency_ms"],
        "avg_latency_ms": adaptive_run.metrics["avg_latency_ms"],
        "p50_latency_ms": adaptive_run.metrics["p50_latency_ms"],
        "p95_latency_ms": adaptive_run.metrics["p95_latency_ms"],
        "p99_latency_ms": adaptive_run.metrics["p99_latency_ms"],
        "benchmark_wall_time_ms": adaptive_run.metrics["benchmark_wall_time_ms"],
        "error_count": adaptive_run.metrics["error_count"],
        "route_distribution": adaptive_run.metrics["route_distribution"],
        "by_route": adaptive_run.metrics["by_route"],
        "by_bucket": adaptive_run.metrics["by_bucket"],
        "relations": adaptive_run.metrics["relations"],
        "llm_call_breakdown": adaptive_run.metrics["llm_call_breakdown"],
        "validation": adaptive_run.metrics["validation"],
        "notes": [],
    }

    if mode == "offline":
        report["notes"].append(
            "Offline mode uses mock LLM callers that return extractor defaults. "
            "Adaptive metrics reflect rule paths; TwoStage quality is not representative."
        )

    if baseline_run is not None:
        report["baseline"] = baseline_run.metrics
        report["comparison"] = _compute_comparison(adaptive_run.metrics, baseline_run.metrics)
    else:
        report["comparison"] = {}

    os.makedirs(output_dir, exist_ok=True)
    suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(output_dir, f"benchmark_report_{suffix}.json")
    empty_path = os.path.join(output_dir, f"empty_episodes_{suffix}.json")
    report["report_path"] = report_path
    report["empty_episodes_path"] = empty_path
    return report, adaptive_run.empty_episodes


def main() -> None:
    args = _parse_args()
    include_buckets = {item.strip() for item in args.buckets.split(",") if item.strip()} or None
    episodes = load_trips_episodes(
        path=args.trips_path,
        max_items=args.max_items,
        include_buckets=include_buckets,
        sort_by_time=not args.no_sort,
    )
    print(f"Loaded episodes: {len(episodes)}")
    if not episodes:
        print("No episodes loaded, stop.")
        return

    if args.online:
        adaptive_run, baseline_run = benchmark_online(
            episodes,
            progress_every=args.progress_every,
            skip_baseline=args.skip_baseline,
        )
        mode = "online"
    else:
        adaptive_run, baseline_run = benchmark_offline(
            episodes,
            progress_every=args.progress_every,
            skip_baseline=args.skip_baseline,
        )
        mode = "offline"

    report, empty_episodes = _build_report(
        adaptive_run=adaptive_run,
        baseline_run=baseline_run,
        mode=mode,
        trips_path=args.trips_path,
        output_dir=args.output_dir,
    )

    with open(report["report_path"], "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    with open(report["empty_episodes_path"], "w", encoding="utf-8") as f:
        json.dump(empty_episodes, f, ensure_ascii=False, indent=2)

    print_report(report)


if __name__ == "__main__":
    main()
