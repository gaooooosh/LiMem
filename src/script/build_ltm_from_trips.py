# -*- coding: utf-8 -*-
"""Build and visually debug dynamic evolution LTM from trips.json."""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from datetime import datetime
from typing import Any

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from limem import create_ltm, migrate_to_dynamic_graph
from script.trips_loader import load_and_split_trips_episodes


def _report_base_path(output_dir: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(output_dir, f"trips_debug_report_{ts}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build dynamic LTM from trips.json with split-phase debugging")
    parser.add_argument(
        "--trips-path",
        default=os.path.join(PROJECT_ROOT, "trips.json"),
        help="Path to trips.json",
    )
    parser.add_argument(
        "--db-path",
        default=os.path.join(PROJECT_ROOT, "DB", "dynamic_trips.kz"),
        help="Output Kuzu DB path",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=0,
        help="Max records to ingest before split (0 means all)",
    )
    parser.add_argument(
        "--debug-max-items",
        type=int,
        default=0,
        help="Max records to replay in the debug phase (0 means all remaining)",
    )
    parser.add_argument(
        "--buckets",
        default="",
        help="Comma-separated bucket names to include (empty means all)",
    )
    parser.add_argument(
        "--split-index",
        type=int,
        default=0,
        help="Absolute split index after sorting. 0 means use split-ratio.",
    )
    parser.add_argument(
        "--split-ratio",
        type=float,
        default=0.7,
        help="Base-phase ratio used when split-index is 0.",
    )
    parser.add_argument(
        "--no-sort",
        action="store_true",
        help="Keep original flattened order instead of sorting by timestamp.",
    )
    parser.add_argument(
        "--online",
        action="store_true",
        help="Use online LLM/embedding mode (default offline)",
    )
    parser.add_argument(
        "--legacy-merge",
        action="store_true",
        help="Disable append-first and use legacy merge strategy",
    )
    parser.add_argument(
        "--clear-db",
        action="store_true",
        help="Delete existing DB file before build",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=100,
        help="Print progress every N episodes",
    )
    parser.add_argument(
        "--debug-snapshot-every",
        type=int,
        default=10,
        help="Capture a debug snapshot every N debug-phase episodes",
    )
    parser.add_argument(
        "--snapshot-limit",
        type=int,
        default=12,
        help="How many events/contexts/patterns to include in each snapshot",
    )
    parser.add_argument(
        "--skip-migration",
        action="store_true",
        help="Skip compatibility migration stage",
    )
    parser.add_argument(
        "--run-consolidation",
        action="store_true",
        help="Run one consolidation pass after debug replay",
    )
    parser.add_argument(
        "--output-dir",
        default=os.path.join(PROJECT_ROOT, "outputs"),
        help="Report output directory",
    )
    return parser.parse_args()


def _episode_to_dict(episode: Any) -> dict[str, Any]:
    return {
        "id": episode.id,
        "content": episode.content,
        "timestamp": episode.timestamp,
        "metadata": episode.metadata,
    }


def _ingest_result_to_dict(result: Any) -> dict[str, Any]:
    return {
        "event_id": result.event.id,
        "summary": result.event.summary,
        "status": result.event.status,
        "is_new": bool(result.is_new),
        "merged_with": result.merged_with,
        "entities_created": int(result.entities_created),
    }


def _capture_snapshot(ltm, limit: int) -> dict[str, Any]:
    return ltm.snapshot(limit=limit, include_inactive=True)


def _run_phase(
    ltm,
    episodes: list[Any],
    phase_name: str,
    progress_every: int,
    capture_every: int = 0,
    snapshot_limit: int = 12,
) -> dict[str, Any]:
    errors = 0
    timeline: list[dict[str, Any]] = []
    for idx, episode in enumerate(episodes, 1):
        timeline_entry = {
            "phase": phase_name,
            "phase_index": idx,
            "episode": _episode_to_dict(episode),
        }
        try:
            result = ltm.ingest(episode)
            timeline_entry["ingest_result"] = _ingest_result_to_dict(result)
        except Exception as ex:  # pragma: no cover - debug flow should keep going
            errors += 1
            timeline_entry["error"] = str(ex)
        if capture_every > 0 and (
            idx == 1 or idx % capture_every == 0 or idx == len(episodes)
        ):
            timeline_entry["stats"] = ltm.get_stats()
            timeline_entry["snapshot"] = _capture_snapshot(ltm, snapshot_limit)
            timeline.append(timeline_entry)
        if progress_every > 0 and idx % progress_every == 0:
            print(f"[{phase_name}] Ingested {idx}/{len(episodes)}")

    return {
        "episodes": len(episodes),
        "errors": errors,
        "timeline": timeline,
        "stats": ltm.get_stats(),
        "snapshot": _capture_snapshot(ltm, snapshot_limit),
    }


def _render_html_report(report: dict[str, Any]) -> str:
    payload = html.escape(json.dumps(report, ensure_ascii=False))
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>LiMem Trips Debug Report</title>
  <style>
    :root {{
      --bg: #f3efe7;
      --panel: #fffaf3;
      --card: #ffffff;
      --line: #ded2bc;
      --text: #2b241c;
      --muted: #756757;
      --accent: #c46b29;
      --accent-soft: #f5dfca;
      --ok: #2c7a51;
      --warn: #b26a1a;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
      background:
        radial-gradient(circle at top left, #fff6e8 0, transparent 34%),
        linear-gradient(180deg, #f6f1e8 0%, #efe7da 100%);
      color: var(--text);
    }}
    .page {{
      max-width: 1320px;
      margin: 0 auto;
      padding: 28px 20px 40px;
    }}
    .hero {{
      background: linear-gradient(135deg, rgba(196,107,41,0.16), rgba(255,255,255,0.88));
      border: 1px solid var(--line);
      border-radius: 24px;
      padding: 24px;
      margin-bottom: 20px;
      box-shadow: 0 16px 40px rgba(80, 54, 28, 0.08);
    }}
    .hero h1 {{
      margin: 0 0 8px;
      font-size: 28px;
      letter-spacing: 0.02em;
    }}
    .hero p {{
      margin: 0;
      color: var(--muted);
      line-height: 1.6;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 14px;
      margin-top: 18px;
    }}
    .metric {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 16px;
    }}
    .metric .label {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .metric .value {{
      margin-top: 8px;
      font-size: 28px;
      font-weight: 700;
    }}
    .section {{
      margin-top: 20px;
      background: rgba(255,255,255,0.68);
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 20px;
      backdrop-filter: blur(6px);
    }}
    .section h2 {{
      margin: 0 0 14px;
      font-size: 20px;
    }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 16px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 16px;
    }}
    .card h3 {{
      margin: 0 0 10px;
      font-size: 16px;
    }}
    .tag {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 4px 10px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
      margin-right: 8px;
      margin-bottom: 8px;
    }}
    .table-wrap {{
      overflow-x: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      text-align: left;
      padding: 10px 8px;
      border-bottom: 1px solid rgba(222,210,188,0.7);
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }}
    .timeline {{
      display: grid;
      gap: 12px;
    }}
    .timeline-item {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 14px;
    }}
    .timeline-item summary {{
      cursor: pointer;
      font-weight: 700;
    }}
    .timeline-item pre {{
      background: #faf4ea;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 12px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-size: 12px;
      color: #40372d;
    }}
    .pill-ok {{ color: var(--ok); }}
    .pill-warn {{ color: var(--warn); }}
    @media (max-width: 860px) {{
      .page {{ padding: 16px; }}
      .hero {{ padding: 18px; }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <div class="hero">
      <h1>LiMem 两段式 Trips 调试报告</h1>
      <p>基础段先构建记忆库，增量段随后逐步回放并记录图快照，便于观察事件、上下文、模式和显式事件语义关系边的动态演进。</p>
      <div class="grid" id="heroMetrics"></div>
    </div>

    <div class="section">
      <h2>阶段概览</h2>
      <div class="cards" id="phaseCards"></div>
    </div>

    <div class="section">
      <h2>最终快照</h2>
      <div class="cards" id="finalCards"></div>
      <div class="table-wrap" id="finalTables"></div>
    </div>

    <div class="section">
      <h2>调试时间线</h2>
      <div class="timeline" id="timeline"></div>
    </div>
  </div>

  <script id="reportData" type="application/json">{payload}</script>
  <script>
    const report = JSON.parse(document.getElementById('reportData').textContent);

    function metric(label, value) {{
      return `<div class="metric"><div class="label">${{label}}</div><div class="value">${{value}}</div></div>`;
    }}

    function tags(items) {{
      return items.map(item => `<span class="tag">${{item}}</span>`).join('');
    }}

    function smallStats(stats) {{
      return `
        <div class="tag">events: ${{stats.event_count || 0}}</div>
        <div class="tag">contexts: ${{stats.context_count || 0}}</div>
        <div class="tag">patterns: ${{stats.pattern_count || 0}}</div>
        <div class="tag">event_rel: ${{stats.event_relation_count || 0}}</div>
      `;
    }}

    function renderTable(title, rows, cols) {{
      const head = cols.map(col => `<th>${{col.label}}</th>`).join('');
      const body = rows.map(row => `
        <tr>${{cols.map(col => `<td>${{col.render(row)}}</td>`).join('')}}</tr>
      `).join('');
      return `
        <div class="card" style="margin-top: 16px;">
          <h3>${{title}}</h3>
          <div class="table-wrap">
            <table>
              <thead><tr>${{head}}</tr></thead>
              <tbody>${{body || '<tr><td colspan="' + cols.length + '">无数据</td></tr>'}}</tbody>
            </table>
          </div>
        </div>
      `;
    }}

    document.getElementById('heroMetrics').innerHTML = [
      metric('总 Episode', report.split.total_episodes),
      metric('基础段', report.split.base_episodes),
      metric('调试段', report.split.debug_episodes),
      metric('切分点', report.split.split_index),
      metric('最终 Event', report.final_stats.event_count || 0),
      metric('最终 Context', report.final_stats.context_count || 0),
    ].join('');

    document.getElementById('phaseCards').innerHTML = `
      <div class="card">
        <h3>基础阶段</h3>
        ${{tags([
          `episodes: ${{report.base_phase.episodes}}`,
          `errors: ${{report.base_phase.errors}}`,
          `split ratio: ${{(report.split.split_ratio * 100).toFixed(1)}}%`,
        ])}}
        <div>${{smallStats(report.base_phase.stats)}}</div>
      </div>
      <div class="card">
        <h3>调试阶段</h3>
        ${{tags([
          `episodes: ${{report.debug_phase.episodes}}`,
          `errors: ${{report.debug_phase.errors}}`,
          `snapshots: ${{report.debug_phase.timeline.length}}`,
        ])}}
        <div>${{smallStats(report.debug_phase.stats)}}</div>
      </div>
      <div class="card">
        <h3>后处理</h3>
        ${{tags([
          `migration: ${{Object.keys(report.migration_applied || {{}}).length ? 'on' : 'off'}}`,
          `consolidation: ${{report.consolidation_report && Object.keys(report.consolidation_report).length ? 'on' : 'off'}}`,
        ])}}
        <div>${{smallStats(report.final_stats)}}</div>
      </div>
    `;

    const finalSnapshot = report.final_snapshot || {{ events: [], contexts: [], patterns: [], edges: {{}} }};
    document.getElementById('finalCards').innerHTML = `
      <div class="card"><h3>事件 / 上下文 / 模式</h3>${{smallStats(report.final_stats)}}</div>
      <div class="card"><h3>边统计</h3>${{tags([
        `event-context: ${{(finalSnapshot.edges?.event_context || []).length}}`,
        `event-pattern: ${{(finalSnapshot.edges?.event_pattern || []).length}}`,
        `next: ${{(finalSnapshot.edges?.next || []).length}}`,
      ])}}</div>
    `;

    document.getElementById('finalTables').innerHTML = [
      renderTable('Events', finalSnapshot.events || [], [
        {{ label: 'ID', render: row => row.id }},
        {{ label: 'Status', render: row => row.status }},
        {{ label: 'Summary', render: row => row.summary }},
        {{ label: 'Contexts', render: row => (row.context_ids || []).join(', ') || '-' }},
        {{ label: 'Patterns', render: row => (row.pattern_ids || []).join(', ') || '-' }},
      ]),
      renderTable('Contexts', finalSnapshot.contexts || [], [
        {{ label: 'ID', render: row => row.id }},
        {{ label: 'Status', render: row => row.status }},
        {{ label: 'Subtype', render: row => row.subtype }},
        {{ label: 'Summary', render: row => row.summary }},
      ]),
      renderTable('Patterns', finalSnapshot.patterns || [], [
        {{ label: 'ID', render: row => row.id }},
        {{ label: 'Status', render: row => row.status }},
        {{ label: 'Type', render: row => row.pattern_type }},
        {{ label: 'Summary', render: row => row.summary }},
      ]),
    ].join('');

    document.getElementById('timeline').innerHTML = (report.debug_phase.timeline || []).map(entry => `
      <details class="timeline-item" open>
        <summary>
          #${{entry.phase_index}}
          · ${{entry.ingest_result?.summary || entry.episode?.content || 'unknown'}}
          · events=${{entry.stats?.event_count || 0}}
          · contexts=${{entry.stats?.context_count || 0}}
          · patterns=${{entry.stats?.pattern_count || 0}}
        </summary>
        <div style="margin-top: 10px;">
          ${{entry.error ? `<div class="pill-warn">Error: ${{entry.error}}</div>` : `<div class="pill-ok">event_id: ${{entry.ingest_result?.event_id || '-'}}</div>`}}
          <pre>${{JSON.stringify(entry, null, 2)}}</pre>
        </div>
      </details>
    `).join('');
  </script>
</body>
</html>"""


def main() -> None:
    args = _parse_args()
    os.makedirs(os.path.dirname(args.db_path), exist_ok=True)
    os.makedirs(args.output_dir, exist_ok=True)

    if args.clear_db and os.path.exists(args.db_path):
        os.remove(args.db_path)

    include_buckets = {x.strip() for x in args.buckets.split(",") if x.strip()} or None
    split_result = load_and_split_trips_episodes(
        path=args.trips_path,
        max_items=args.max_items,
        include_buckets=include_buckets,
        sort_by_time=not args.no_sort,
        split_index=args.split_index,
        split_ratio=args.split_ratio,
        debug_max_items=args.debug_max_items,
    )

    print(
        "Loaded episodes:",
        split_result.total_episodes,
        f"(base={len(split_result.base_episodes)}, debug={len(split_result.debug_episodes)})",
    )
    if split_result.total_episodes == 0:
        print("No episodes loaded, stop.")
        return

    ltm = create_ltm(
        db_path=args.db_path,
        config={
            "offline_mode": not args.online,
            "enable_dynamic_evolution": True,
            "append_first_mode": not args.legacy_merge,
            "generate_answer": False,
            "search_top_k": 5,
        },
    )

    base_phase = _run_phase(
        ltm=ltm,
        episodes=split_result.base_episodes,
        phase_name="base",
        progress_every=args.progress_every,
        capture_every=0,
        snapshot_limit=args.snapshot_limit,
    )
    debug_phase = _run_phase(
        ltm=ltm,
        episodes=split_result.debug_episodes,
        phase_name="debug",
        progress_every=args.progress_every,
        capture_every=max(args.debug_snapshot_every, 0),
        snapshot_limit=args.snapshot_limit,
    )

    migration_dry = {}
    migration_run = {}
    if not args.skip_migration:
        dry_report = migrate_to_dynamic_graph(ltm.store, dry_run=True)
        run_report = migrate_to_dynamic_graph(ltm.store, dry_run=False)
        migration_dry = dry_report.to_dict()
        migration_run = run_report.to_dict()
        print("Migration dry-run:", migration_dry)
        print("Migration applied:", migration_run)

    consolidation_report: dict[str, Any] = {}
    if args.run_consolidation:
        consolidation_report = ltm.run_consolidation()
        print("Consolidation report:", consolidation_report)

    final_stats = ltm.get_stats()
    final_snapshot = _capture_snapshot(ltm, args.snapshot_limit)
    print("Stats:", final_stats)

    report = {
        "trips_path": args.trips_path,
        "db_path": args.db_path,
        "offline_mode": not args.online,
        "append_first_mode": not args.legacy_merge,
        "split": {
            "split_index": split_result.split_index,
            "split_ratio": split_result.split_ratio,
            "total_episodes": split_result.total_episodes,
            "base_episodes": len(split_result.base_episodes),
            "debug_episodes": len(split_result.debug_episodes),
        },
        "base_phase": base_phase,
        "debug_phase": debug_phase,
        "migration_dry_run": migration_dry,
        "migration_applied": migration_run,
        "consolidation_report": consolidation_report,
        "final_stats": final_stats,
        "final_snapshot": final_snapshot,
    }

    base_path = _report_base_path(args.output_dir)
    json_path = base_path + ".json"
    html_path = base_path + ".html"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(_render_html_report(report))
    print(f"Debug report saved: {json_path}")
    print(f"Visual report saved: {html_path}")


if __name__ == "__main__":
    main()
