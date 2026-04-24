# -*- coding: utf-8 -*-
"""图拓扑可视化器

从 Kuzu 图数据库提取数据并生成交互式 HTML 可视化。
支持深色/浅色双主题切换、搜索高亮、节点类型过滤、IN_REL 边渲染。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import kuzu


@dataclass
class VisualizationConfig:
    """可视化配置"""

    # 节点限制
    max_events: int = 100
    max_entities: int = 50
    max_contexts: int = 20

    # 节点大小
    event_base_size: float = 8.0
    event_size_per_support: float = 2.0
    entity_size: float = 10.0
    context_size: float = 8.0

    # 颜色 (十六进制)
    event_color: str = "#FF6B6B"
    event_stroke: str = "#DC143C"
    entity_color: str = "#4ECDC4"
    entity_stroke: str = "#2E8B57"
    context_color: str = "#95E1D3"
    context_stroke: str = "#228B22"
    involves_edge_color: str = "#666666"
    relation_edge_color: str = "#FF9F1C"
    in_rel_edge_color: str = "#2f7975"

    # 力导向参数
    link_distance: float = 120.0
    charge_strength: float = -300.0
    collision_radius: float = 35.0

    # 视图
    width: int = 1200
    height: int = 800
    background_color: str = "#1a1a2e"

    # 主题: "light" 或 "dark"
    theme: str = "light"


def _normalize_display_value(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    mapped = {
        "unknown": "未知",
        "context": "上下文",
        "event": "事件",
        "entity": "实体",
        "active": "活跃",
        "merged": "已合并",
    }
    return mapped.get(text.lower(), text)


class GraphVisualizer:
    """图拓扑可视化器

    从 LiMem 图数据库提取节点和边数据，生成交互式 HTML 可视化。

    使用方式:
        visualizer = GraphVisualizer(db_path="./my_memory.kz")
        visualizer.export_html("./viz/graph.html")

        # 或使用自定义配置
        config = VisualizationConfig(max_events=50, theme="dark")
        visualizer = GraphVisualizer(db_path="./my_memory.kz", config=config)
        html = visualizer.generate_html()
    """

    def __init__(
        self,
        db_path: str | Path,
        config: Optional[VisualizationConfig] = None,
        read_only: bool = True,
    ):
        self.db_path = str(db_path)
        self.config = config or VisualizationConfig()
        self.read_only = read_only
        self._db: Optional[kuzu.Database] = None
        self._conn: Optional[kuzu.Connection] = None

    def _get_connection(self) -> kuzu.Connection:
        if self._conn is None:
            self._db = kuzu.Database(self.db_path, read_only=self.read_only)
            self._conn = kuzu.Connection(self._db)
        return self._conn

    def _close_connection(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        if self._db is not None:
            self._db = None

    def extract_graph_data(self) -> dict[str, Any]:
        """从数据库提取图数据"""
        conn = self._get_connection()
        cfg = self.config

        data: dict[str, Any] = {"nodes": [], "links": []}
        node_ids: set[str] = set()

        # 1. 提取活跃事件节点 (含完整字段)
        result = conn.execute(f"""
            MATCH (e:Event)
            WHERE e.status = 'active'
            RETURN e.id, e.summary, e.action, e.support_count, e.timestamp,
                   e.last_active, e.status, e.participants, e.causality,
                   e.payload, e.time_range
            ORDER BY e.support_count DESC
            LIMIT {cfg.max_events}
        """)
        while result.has_next():
            row = result.get_next()
            nid = row[0][:16] if row[0] else f"ev_{len(node_ids)}"
            node_ids.add(nid)
            summary = row[1] or ""
            data["nodes"].append({
                "id": nid,
                "full_id": row[0],
                "type": "event",
                "label": summary[:30] + "..." if len(summary) > 30 else summary or row[2] or "事件",
                "summary": summary,
                "support": row[3] or 1,
                "action": row[2] or "",
                "timestamp": row[4],
                "last_active": row[5],
                "status": _normalize_display_value(row[6], fallback="活跃"),
                "participants": row[7] or "",
                "causality": row[8] or "",
                "payload": row[9] or "",
                "time_range": row[10] or "",
            })

        # 2. 提取实体节点
        result = conn.execute(f"""
            MATCH (en:Entity)
            RETURN en.id, en.type
            ORDER BY en.id
            LIMIT {cfg.max_entities}
        """)
        while result.has_next():
            row = result.get_next()
            nid = row[0] if row[0] else f"en_{len(node_ids)}"
            node_ids.add(nid)
            data["nodes"].append({
                "id": nid,
                "full_id": row[0],
                "type": "entity",
                "label": nid[:20],
                "entity_type": _normalize_display_value(row[1], fallback="未知"),
            })

        # 3. 提取上下文节点 (含完整字段)
        result = conn.execute(f"""
            MATCH (c:Context)
            WHERE c.status = 'active'
            RETURN c.id, c.summary, c.context_type, c.subtype,
                   c.description, c.confidence, c.support_count,
                   c.created_at, c.last_seen_at, c.status
            LIMIT {cfg.max_contexts}
        """)
        while result.has_next():
            row = result.get_next()
            nid = row[0][:16] if row[0] else f"ctx_{len(node_ids)}"
            node_ids.add(nid)
            summary = row[1] or ""
            data["nodes"].append({
                "id": nid,
                "full_id": row[0],
                "type": "context",
                "label": summary[:25] + "..." if len(summary) > 25 else summary or "上下文",
                "summary": summary,
                "context_type": _normalize_display_value(row[2], fallback="上下文"),
                "subtype": row[3] or "",
                "description": row[4] or "",
                "confidence": row[5],
                "support_count": row[6],
                "created_at": row[7],
                "last_seen_at": row[8],
                "status": _normalize_display_value(row[9], fallback="活跃"),
            })

        # 4. 提取 INVOLVES 边
        result = conn.execute("""
            MATCH (e:Event)-[r:INVOLVES]->(en:Entity)
            WHERE e.status = 'active'
            RETURN e.id, en.id
        """)
        while result.has_next():
            row = result.get_next()
            src = row[0][:16] if row[0] else None
            tgt = row[1] if row[1] else None
            if src in node_ids and tgt in node_ids:
                data["links"].append({"source": src, "target": tgt, "type": "involves"})

        # 5. 提取 EVENT_RELATION 边 (含完整字段)
        try:
            result = conn.execute("""
                MATCH (e1:Event)-[r:EVENT_RELATION]->(e2:Event)
                WHERE e1.status = 'active' AND e2.status = 'active'
                RETURN e1.id, e2.id, r.relation_type, r.description, r.confidence
            """)
            while result.has_next():
                row = result.get_next()
                src = row[0][:16] if row[0] else None
                tgt = row[1][:16] if row[1] else None
                if src in node_ids and tgt in node_ids:
                    data["links"].append({
                        "source": src,
                        "target": tgt,
                        "type": "relation",
                        "rel": row[2] if len(row) > 2 and row[2] else "关联",
                        "description": row[3] if len(row) > 3 else "",
                        "confidence": row[4] if len(row) > 4 else None,
                    })
        except Exception:
            pass

        # 6. 提取 IN_REL 边 (含完整字段)
        try:
            result = conn.execute("""
                MATCH (e:Event)-[r:IN_REL]->(c:Context)
                WHERE e.status = 'active' AND c.status = 'active'
                RETURN e.id, c.id, r.confidence, r.original_signal, r.weight,
                       r.created_at, r.last_seen_at
            """)
            while result.has_next():
                row = result.get_next()
                src = row[0][:16] if row[0] else None
                tgt = row[1][:16] if row[1] else None
                if src in node_ids and tgt in node_ids:
                    data["links"].append({
                        "source": src,
                        "target": tgt,
                        "type": "in_rel",
                        "confidence": row[2] if len(row) > 2 else None,
                        "signal": row[3] if len(row) > 3 else None,
                        "weight": row[4] if len(row) > 4 else None,
                        "created_at": row[5] if len(row) > 5 else None,
                        "last_seen_at": row[6] if len(row) > 6 else None,
                    })
        except Exception:
            pass

        return data

    def generate_html(
        self,
        title: str = "LiMem 图拓扑可视化",
        include_d3_local: bool = True,
    ) -> str:
        """生成可视化 HTML"""
        data = self.extract_graph_data()
        cfg = self.config

        d3_src = "d3.min.js" if include_d3_local else "https://d3js.org/d3.v7.min.js"

        stats = {
            "events": sum(1 for n in data["nodes"] if n["type"] == "event"),
            "entities": sum(1 for n in data["nodes"] if n["type"] == "entity"),
            "contexts": sum(1 for n in data["nodes"] if n["type"] == "context"),
            "edges": len(data["links"]),
        }

        default_theme = cfg.theme

        html_str = f'''<!DOCTYPE html>
<html data-theme="{default_theme}">
<head>
    <meta charset="UTF-8">
    <title>{title}</title>
    <script src="{d3_src}"></script>
    <style>
        :root {{
            --sidebar-w: 250px;
            --detail-w: 340px;
        }}
        [data-theme="light"] {{
            --bg: #f2eadb;
            --panel: rgba(255,252,247,0.86);
            --text: #241d16;
            --text-muted: #756757;
            --accent: #b95d2d;
            --accent-soft: #f5dfca;
            --border: #ded2bc;
            --node-label: #241d16;
            --tooltip-bg: rgba(255,252,247,0.96);
            --tooltip-border: #b95d2d;
            --btn-bg: #b95d2d;
            --btn-text: #fff;
            --btn-hover: #a04e24;
            --search-bg: rgba(255,255,255,0.7);
            --search-border: #ded2bc;
            --event-color: #e05a4f;
            --event-stroke: #b83a30;
            --entity-color: #3ab0a0;
            --entity-stroke: #267a6e;
            --context-color: #6dbfaa;
            --context-stroke: #3e8a6e;
            --card-bg: rgba(255,255,255,0.72);
        }}
        [data-theme="dark"] {{
            --bg: #1a1a2e;
            --panel: rgba(30,30,50,0.95);
            --text: #eee;
            --text-muted: #999;
            --accent: #4ECDC4;
            --accent-soft: rgba(78,205,196,0.15);
            --border: #333;
            --node-label: #fff;
            --tooltip-bg: rgba(20,20,40,0.95);
            --tooltip-border: #4ECDC4;
            --btn-bg: #4ECDC4;
            --btn-text: #1a1a2e;
            --btn-hover: #3dbdb5;
            --search-bg: rgba(255,255,255,0.08);
            --search-border: #444;
            --event-color: {cfg.event_color};
            --event-stroke: {cfg.event_stroke};
            --entity-color: {cfg.entity_color};
            --entity-stroke: {cfg.entity_stroke};
            --context-color: {cfg.context_color};
            --context-stroke: {cfg.context_stroke};
            --card-bg: rgba(255,255,255,0.06);
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: "Noto Sans SC", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            overflow: hidden;
            transition: background 0.3s, color 0.3s;
        }}
        #container {{ width: 100vw; height: 100vh; }}

        .sidebar {{
            position: fixed;
            top: 14px;
            left: 14px;
            background: var(--panel);
            backdrop-filter: blur(6px);
            -webkit-backdrop-filter: blur(6px);
            padding: 18px;
            border-radius: 22px;
            width: var(--sidebar-w);
            border: 1px solid var(--border);
            box-shadow: 0 8px 32px rgba(0,0,0,0.12);
            z-index: 100;
            max-height: calc(100vh - 28px);
            overflow-y: auto;
        }}
        .sidebar h2 {{
            font-size: 15px;
            margin-bottom: 14px;
            color: var(--accent);
            display: flex;
            align-items: center;
            gap: 6px;
        }}

        .search-box {{
            width: 100%;
            padding: 7px 10px;
            border-radius: 10px;
            border: 1px solid var(--search-border);
            background: var(--search-bg);
            color: var(--text);
            font-size: 12px;
            outline: none;
            margin-bottom: 12px;
            transition: border-color 0.2s;
        }}
        .search-box:focus {{ border-color: var(--accent); }}
        .search-box::placeholder {{ color: var(--text-muted); }}

        .filter-group {{ margin-bottom: 12px; }}
        .filter-group label {{
            display: flex;
            align-items: center;
            margin: 5px 0;
            font-size: 12px;
            cursor: pointer;
            gap: 6px;
        }}
        .filter-group input[type="checkbox"] {{
            accent-color: var(--accent);
            width: 14px;
            height: 14px;
        }}
        .badge {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 20px;
            height: 18px;
            padding: 0 5px;
            border-radius: 999px;
            background: var(--accent-soft);
            color: var(--accent);
            font-size: 10px;
            font-weight: 700;
            margin-left: auto;
        }}

        .legend-item {{ display: flex; align-items: center; margin: 5px 0; font-size: 12px; }}
        .legend-dot {{ width: 12px; height: 12px; border-radius: 50%; margin-right: 8px; flex-shrink: 0; }}
        .legend-line {{
            width: 20px;
            height: 0;
            margin-right: 8px;
            flex-shrink: 0;
        }}
        .separator {{
            border-top: 1px solid var(--border);
            margin: 10px 0;
        }}

        .stats {{ }}
        .stat-row {{
            display: flex;
            justify-content: space-between;
            font-size: 12px;
            margin: 4px 0;
        }}
        .stat-value {{ color: var(--accent); font-weight: bold; }}

        .controls {{
            position: fixed;
            bottom: 14px;
            left: 14px;
            display: flex;
            gap: 6px;
            z-index: 100;
        }}
        .controls button {{
            background: var(--btn-bg);
            color: var(--btn-text);
            border: none;
            padding: 7px 14px;
            border-radius: 12px;
            cursor: pointer;
            font-size: 11px;
            font-weight: 600;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            transition: background 0.2s;
        }}
        .controls button:hover {{ background: var(--btn-hover); }}

        .node {{ cursor: pointer; }}
        .node circle {{
            stroke-width: 2px;
            transition: opacity 0.3s, stroke-width 0.2s;
        }}
        .node:hover circle {{
            stroke-width: 4px;
            filter: brightness(1.2);
        }}
        .node text {{
            font-size: 9px;
            fill: var(--node-label);
            pointer-events: none;
            transition: opacity 0.3s;
        }}
        [data-theme="dark"] .node text {{
            text-shadow: 0 0 3px #000;
        }}

        .link {{ stroke-opacity: 0.4; transition: opacity 0.3s, stroke-width 0.2s; cursor: pointer; }}
        .link-in-rel {{ stroke-dasharray: 5,3; }}
        .link.link-selected {{ stroke-opacity: 1; }}
        .link.link-neighbor {{ stroke-opacity: 0.85; }}

        .tooltip {{
            position: fixed;
            background: var(--tooltip-bg);
            border: 1px solid var(--tooltip-border);
            border-radius: 10px;
            padding: 12px;
            font-size: 11px;
            max-width: 280px;
            pointer-events: none;
            z-index: 1000;
            display: none;
            box-shadow: 0 4px 16px rgba(0,0,0,0.15);
            backdrop-filter: blur(4px);
        }}
        .tooltip .tip-title {{
            color: var(--accent);
            font-weight: bold;
            margin-bottom: 6px;
            font-size: 12px;
        }}
        .tooltip .tip-row {{
            margin: 2px 0;
            color: var(--text-muted);
        }}
        .tooltip .tip-row span {{
            color: var(--text);
            font-weight: 500;
        }}

        .node.dimmed circle {{ opacity: 0.15; }}
        .node.dimmed text {{ opacity: 0; }}
        .link.dimmed {{ opacity: 0.05; }}
        .node.highlighted circle {{
            stroke-width: 4px;
            filter: brightness(1.3) drop-shadow(0 0 6px var(--accent));
        }}
        .node.selected circle {{
            stroke-width: 5px;
            filter: brightness(1.3) drop-shadow(0 0 8px var(--accent));
        }}
        .node.neighbor circle {{
            stroke-width: 3.5px;
            filter: brightness(1.15) drop-shadow(0 0 4px var(--accent));
        }}

        /* Detail panel */
        .detail-panel {{
            position: fixed;
            top: 14px;
            right: 14px;
            width: var(--detail-w);
            max-height: calc(100vh - 28px);
            background: var(--panel);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            border: 1px solid var(--border);
            border-radius: 22px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.12);
            z-index: 100;
            overflow-y: auto;
            transform: translateX(calc(var(--detail-w) + 20px));
            opacity: 0;
            transition: transform 0.3s ease, opacity 0.3s ease;
        }}
        .detail-panel.visible {{
            transform: translateX(0);
            opacity: 1;
        }}
        .detail-header {{
            padding: 16px 18px 0;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}
        .detail-badge {{
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 5px 12px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 700;
            color: #fff;
        }}
        .detail-badge.event {{ background: var(--event-color); }}
        .detail-badge.entity {{ background: var(--entity-color); }}
        .detail-badge.context {{ background: var(--context-color); }}
        .detail-badge.edge-involves {{ background: var(--text-muted); }}
        .detail-badge.edge-relation {{ background: #FF9F1C; }}
        .detail-badge.edge-in_rel {{ background: #2f7975; }}
        .detail-close {{
            width: 28px;
            height: 28px;
            border-radius: 50%;
            border: 1px solid var(--border);
            background: var(--card-bg);
            color: var(--text-muted);
            font-size: 14px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: background 0.15s;
        }}
        .detail-close:hover {{ background: var(--accent-soft); color: var(--accent); }}
        .detail-body {{
            padding: 14px 18px 18px;
        }}
        .detail-summary {{
            font-size: 15px;
            font-weight: 800;
            line-height: 1.5;
            margin-bottom: 10px;
        }}
        .detail-id {{
            font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
            font-size: 10px;
            color: var(--text-muted);
            background: var(--card-bg);
            border-radius: 8px;
            padding: 4px 8px;
            word-break: break-all;
            margin-bottom: 12px;
        }}
        .detail-section {{
            margin-bottom: 12px;
        }}
        .detail-section-title {{
            font-size: 10px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: var(--text-muted);
            margin-bottom: 6px;
            padding-bottom: 4px;
            border-bottom: 1px solid var(--border);
        }}
        .detail-kv {{
            display: grid;
            grid-template-columns: 78px 1fr;
            gap: 4px;
            font-size: 12px;
            line-height: 1.5;
            padding: 6px 0;
            border-bottom: 1px dashed var(--border);
        }}
        .detail-kv:last-child {{ border-bottom: none; }}
        .detail-kv-key {{
            color: var(--text-muted);
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }}
        .detail-json {{
            white-space: pre-wrap;
            word-break: break-word;
            font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
            font-size: 11px;
            line-height: 1.5;
            border-radius: 12px;
            background: var(--card-bg);
            border: 1px solid var(--border);
            padding: 8px;
            max-height: 150px;
            overflow-y: auto;
        }}
        .detail-bar {{
            height: 5px;
            border-radius: 3px;
            background: var(--card-bg);
            overflow: hidden;
            margin-top: 3px;
        }}
        .detail-bar-fill {{
            height: 100%;
            border-radius: 3px;
        }}
        .detail-conn {{
            display: grid;
            gap: 6px;
        }}
        .detail-conn-item {{
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 10px;
            border-radius: 12px;
            background: var(--card-bg);
            border: 1px solid var(--border);
            cursor: pointer;
            transition: background 0.15s;
            font-size: 12px;
        }}
        .detail-conn-item:hover {{
            background: var(--accent-soft);
            border-color: var(--accent);
        }}
        .detail-conn-dot {{
            width: 9px;
            height: 9px;
            border-radius: 50%;
            flex-shrink: 0;
        }}
        .detail-conn-dot.event {{ background: var(--event-color); }}
        .detail-conn-dot.entity {{ background: var(--entity-color); }}
        .detail-conn-dot.context {{ background: var(--context-color); }}
        .detail-conn-info {{
            min-width: 0;
        }}
        .detail-conn-label {{
            font-weight: 700;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .detail-conn-meta {{
            font-size: 10px;
            color: var(--text-muted);
        }}
        .detail-edge-arrow {{
            text-align: center;
            color: var(--text-muted);
            font-size: 16px;
            padding: 2px 0;
        }}
        .detail-endpoint {{
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 10px;
            border-radius: 12px;
            background: var(--card-bg);
            border: 1px solid var(--border);
            cursor: pointer;
            transition: background 0.15s;
        }}
        .detail-endpoint:hover {{
            background: var(--accent-soft);
            border-color: var(--accent);
        }}
        .detail-endpoint-label {{
            font-size: 10px;
            color: var(--text-muted);
            text-transform: uppercase;
        }}
        .detail-endpoint-text {{
            font-size: 12px;
            font-weight: 700;
        }}
    </style>
</head>
<body>
    <div class="sidebar">
        <h2>{title}</h2>

        <input type="text" class="search-box" id="searchInput"
               placeholder="搜索节点..." oninput="onSearch(this.value)">

        <div class="filter-group">
            <label>
                <input type="checkbox" checked onchange="toggleFilter('event', this.checked)">
                <div class="legend-dot" style="background:var(--event-color)"></div>
                事件
                <span class="badge" id="badge-event">{stats['events']}</span>
            </label>
            <label>
                <input type="checkbox" checked onchange="toggleFilter('entity', this.checked)">
                <div class="legend-dot" style="background:var(--entity-color)"></div>
                实体
                <span class="badge" id="badge-entity">{stats['entities']}</span>
            </label>
            <label>
                <input type="checkbox" checked onchange="toggleFilter('context', this.checked)">
                <div class="legend-dot" style="background:var(--context-color)"></div>
                上下文
                <span class="badge" id="badge-context">{stats['contexts']}</span>
            </label>
        </div>

        <div class="separator"></div>

        <div style="font-size:11px;color:var(--text-muted);margin-bottom:6px;">边类型</div>
        <div class="legend-item">
            <svg class="legend-line" viewBox="0 0 20 2"><line x1="0" y1="1" x2="20" y2="1" stroke="{cfg.involves_edge_color}" stroke-width="1.5"/></svg>
            实体关联（INVOLVES）
        </div>
        <div class="legend-item">
            <svg class="legend-line" viewBox="0 0 20 2"><line x1="0" y1="1" x2="20" y2="1" stroke="{cfg.relation_edge_color}" stroke-width="2"/></svg>
            事件关系（EVENT_RELATION）
        </div>
        <div class="legend-item">
            <svg class="legend-line" viewBox="0 0 20 2"><line x1="0" y1="1" x2="20" y2="1" stroke="{cfg.in_rel_edge_color}" stroke-width="1.5" stroke-dasharray="4,2"/></svg>
            上下文关联（IN_REL）
        </div>

        <div class="separator"></div>

        <div class="stats">
            <div class="stat-row"><span>节点总数</span><span class="stat-value">{stats['events'] + stats['entities'] + stats['contexts']}</span></div>
            <div class="stat-row"><span>边数量</span><span class="stat-value">{stats['edges']}</span></div>
        </div>
        <div class="separator"></div>
        <div style="font-size:11px;color:var(--text-muted);line-height:1.5;">点击节点或边查看详情</div>
    </div>

    <div class="detail-panel" id="detailPanel">
        <div class="detail-header">
            <div id="detailBadge"></div>
            <button class="detail-close" onclick="clearSelection()">&times;</button>
        </div>
        <div class="detail-body" id="detailBody"></div>
    </div>

    <div class="controls">
        <button onclick="resetZoom()">重置视图</button>
        <button onclick="toggleTheme()">切换主题</button>
    </div>

    <div id="container"></div>
    <div class="tooltip" id="tooltip"></div>

    <script>
    const graphData = {json.dumps(data, ensure_ascii=False)};
    const width = window.innerWidth;
    const height = window.innerHeight;

    // Index: node by id
    const nodeMap = {{}};
    graphData.nodes.forEach(n => {{ nodeMap[n.id] = n; }});

    // Build adjacency for connection count
    const connCount = {{}};
    const adjacency = {{}};
    graphData.links.forEach((l, idx) => {{
        const s = typeof l.source === 'object' ? l.source.id : l.source;
        const t = typeof l.target === 'object' ? l.target.id : l.target;
        connCount[s] = (connCount[s] || 0) + 1;
        connCount[t] = (connCount[t] || 0) + 1;
        if (!adjacency[s]) adjacency[s] = [];
        if (!adjacency[t]) adjacency[t] = [];
        adjacency[s].push({{ neighbor: t, linkIdx: idx, link: l }});
        adjacency[t].push({{ neighbor: s, linkIdx: idx, link: l }});
    }});

    let selectedNodeId = null;
    let selectedLinkIdx = null;

    const svg = d3.select('#container').append('svg')
        .attr('width', width)
        .attr('height', height);

    const g = svg.append('g');

    const zoomBehavior = d3.zoom()
        .scaleExtent([0.1, 4])
        .on('zoom', (event) => g.attr('transform', event.transform));
    svg.call(zoomBehavior);

    // Click on background to deselect
    svg.on('click', function(event) {{
        if (event.target.tagName === 'svg') clearSelection();
    }});

    function getThemeColors() {{
        const cs = getComputedStyle(document.documentElement);
        return {{
            event: cs.getPropertyValue('--event-color').trim(),
            entity: cs.getPropertyValue('--entity-color').trim(),
            context: cs.getPropertyValue('--context-color').trim(),
            eventStroke: cs.getPropertyValue('--event-stroke').trim(),
            entityStroke: cs.getPropertyValue('--entity-stroke').trim(),
            contextStroke: cs.getPropertyValue('--context-stroke').trim(),
            nodeLabel: cs.getPropertyValue('--node-label').trim(),
        }};
    }}

    function nodeTypeLabel(type) {{
        const labels = {{ event: '事件', entity: '实体', context: '上下文' }};
        return labels[type] || type;
    }}

    function edgeTypeLabel(type) {{
        const labels = {{
            involves: '实体关联（INVOLVES）',
            relation: '事件关系（EVENT_RELATION）',
            in_rel: '上下文关联（IN_REL）'
        }};
        return labels[type] || type;
    }}

    const simulation = d3.forceSimulation(graphData.nodes)
        .force('link', d3.forceLink(graphData.links).id(d => d.id).distance({cfg.link_distance}))
        .force('charge', d3.forceManyBody().strength({cfg.charge_strength}))
        .force('center', d3.forceCenter(width / 2, height / 2))
        .force('collision', d3.forceCollide().radius({cfg.collision_radius}));

    // Draw links (with invisible wider hit area)
    const linkG = g.append('g');
    const linkHit = linkG.selectAll('line.link-hit')
        .data(graphData.links)
        .enter().append('line')
        .attr('class', 'link-hit')
        .attr('stroke', 'transparent')
        .attr('stroke-width', 12)
        .style('cursor', 'pointer')
        .on('click', function(event, d) {{
            event.stopPropagation();
            const idx = graphData.links.indexOf(d);
            selectLink(idx);
        }});

    const link = linkG.selectAll('line.link')
        .data(graphData.links)
        .enter().append('line')
        .attr('class', d => 'link' + (d.type === 'in_rel' ? ' link-in-rel' : ''))
        .attr('stroke', d => {{
            if (d.type === 'relation') return '{cfg.relation_edge_color}';
            if (d.type === 'in_rel') return '{cfg.in_rel_edge_color}';
            return '{cfg.involves_edge_color}';
        }})
        .attr('stroke-width', d => d.type === 'relation' ? 2 : 1.5)
        .style('pointer-events', 'none');

    // Draw nodes
    const node = g.append('g')
        .selectAll('g')
        .data(graphData.nodes)
        .enter().append('g')
        .attr('class', 'node')
        .call(d3.drag()
            .on('start', dragstarted)
            .on('drag', dragged)
            .on('end', dragended));

    node.append('circle')
        .attr('r', d => {{
            if (d.type === 'event') return {cfg.event_base_size} + (d.support || 1) * {cfg.event_size_per_support};
            if (d.type === 'entity') return {cfg.entity_size};
            return {cfg.context_size};
        }});

    function applyNodeColors() {{
        const tc = getThemeColors();
        const colorMap = {{ event: tc.event, entity: tc.entity, context: tc.context }};
        const strokeMap = {{ event: tc.eventStroke, entity: tc.entityStroke, context: tc.contextStroke }};
        node.select('circle')
            .attr('fill', d => colorMap[d.type] || '#999')
            .attr('stroke', d => strokeMap[d.type] || '#666');
        labels.attr('fill', tc.nodeLabel);
    }}

    // Smart labels
    const labels = node.append('text')
        .attr('dy', -12)
        .attr('text-anchor', 'middle')
        .text(d => d.label || d.id)
        .style('display', d => {{
            if (d.type === 'event' && (d.support || 1) >= 2) return 'block';
            if (d.type === 'entity') return 'block';
            return 'none';
        }});

    // Node click → select
    node.on('click', function(event, d) {{
        event.stopPropagation();
        selectNode(d.id);
    }});

    // Hover tooltip (only when nothing selected or for quick peek)
    node.on('mouseover', function(event, d) {{
        d3.select(this).select('text').style('display', 'block');
        if (selectedNodeId || selectedLinkIdx !== null) return;
        const conn = connCount[d.id] || 0;
        const tip = document.getElementById('tooltip');
        let rows = '<div class="tip-title">' + esc(d.label || d.id) + '</div>';
        rows += '<div class="tip-row">类型: <span>' + nodeTypeLabel(d.type) + '</span></div>';
        if (d.support) rows += '<div class="tip-row">支撑次数: <span>' + d.support + '</span></div>';
        if (d.action) rows += '<div class="tip-row">动作: <span>' + d.action + '</span></div>';
        if (d.entity_type) rows += '<div class="tip-row">实体类型: <span>' + d.entity_type + '</span></div>';
        if (d.context_type) rows += '<div class="tip-row">上下文类型: <span>' + d.context_type + '</span></div>';
        rows += '<div class="tip-row">连接数: <span>' + conn + '</span></div>';
        rows += '<div class="tip-row" style="color:var(--accent);margin-top:4px;">点击查看详情</div>';
        tip.innerHTML = rows;
        tip.style.display = 'block';
        tip.style.left = (event.pageX + 15) + 'px';
        tip.style.top = (event.pageY - 10) + 'px';
    }}).on('mousemove', function(event) {{
        if (selectedNodeId || selectedLinkIdx !== null) return;
        const tip = document.getElementById('tooltip');
        tip.style.left = (event.pageX + 15) + 'px';
        tip.style.top = (event.pageY - 10) + 'px';
    }}).on('mouseout', function(event, d) {{
        const show = (d.type === 'event' && (d.support || 1) >= 2) || d.type === 'entity';
        if (!searchActive && !selectedNodeId) d3.select(this).select('text').style('display', show ? 'block' : 'none');
        document.getElementById('tooltip').style.display = 'none';
    }});

    simulation.on('tick', () => {{
        link.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
            .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
        linkHit.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
               .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
        node.attr('transform', d => 'translate(' + d.x + ',' + d.y + ')');
    }});

    function dragstarted(event) {{
        if (!event.active) simulation.alphaTarget(0.3).restart();
        event.subject.fx = event.subject.x;
        event.subject.fy = event.subject.y;
    }}
    function dragged(event) {{
        event.subject.fx = event.x;
        event.subject.fy = event.y;
    }}
    function dragended(event) {{
        if (!event.active) simulation.alphaTarget(0);
        event.subject.fx = null;
        event.subject.fy = null;
    }}

    function resetZoom() {{
        svg.transition().duration(500).call(zoomBehavior.transform, d3.zoomIdentity);
    }}

    function toggleTheme() {{
        const html = document.documentElement;
        const current = html.getAttribute('data-theme');
        html.setAttribute('data-theme', current === 'dark' ? 'light' : 'dark');
        applyNodeColors();
    }}

    const filterState = {{ event: true, entity: true, context: true }};
    function toggleFilter(type, checked) {{
        filterState[type] = checked;
        applyFilters();
    }}
    function applyFilters() {{
        node.style('display', d => filterState[d.type] ? null : 'none');
        link.style('display', d => {{
            const src = typeof d.source === 'object' ? d.source : graphData.nodes.find(n => n.id === d.source);
            const tgt = typeof d.target === 'object' ? d.target : graphData.nodes.find(n => n.id === d.target);
            if (!src || !tgt) return 'none';
            return (filterState[src.type] && filterState[tgt.type]) ? null : 'none';
        }});
    }}

    let searchActive = false;
    function onSearch(query) {{
        query = query.trim().toLowerCase();
        if (!query) {{
            searchActive = false;
            node.classed('dimmed', false).classed('highlighted', false);
            link.classed('dimmed', false);
            labels.style('display', d => {{
                if (d.type === 'event' && (d.support || 1) >= 2) return 'block';
                if (d.type === 'entity') return 'block';
                return 'none';
            }});
            return;
        }}
        searchActive = true;
        const matchIds = new Set();
        graphData.nodes.forEach(n => {{
            const text = (n.label || '') + ' ' + (n.id || '') + ' ' + (n.action || '') + ' ' + (n.entity_type || '') + ' ' + (n.context_type || '') + ' ' + (n.summary || '');
            if (text.toLowerCase().includes(query)) matchIds.add(n.id);
        }});
        node.classed('highlighted', d => matchIds.has(d.id));
        node.classed('dimmed', d => !matchIds.has(d.id));
        node.select('text').style('display', d => matchIds.has(d.id) ? 'block' : 'none');
        link.classed('dimmed', d => {{
            const sId = typeof d.source === 'object' ? d.source.id : d.source;
            const tId = typeof d.target === 'object' ? d.target.id : d.target;
            return !matchIds.has(sId) && !matchIds.has(tId);
        }});
    }}

    // ── Selection & Detail Panel ──

    function selectNode(nodeId) {{
        selectedNodeId = nodeId;
        selectedLinkIdx = null;
        document.getElementById('tooltip').style.display = 'none';
        applySelectionHighlight();
        renderNodeDetail(nodeId);
    }}

    function selectLink(idx) {{
        selectedLinkIdx = idx;
        selectedNodeId = null;
        document.getElementById('tooltip').style.display = 'none';
        applySelectionHighlight();
        renderLinkDetail(idx);
    }}

    function clearSelection() {{
        selectedNodeId = null;
        selectedLinkIdx = null;
        applySelectionHighlight();
        hideDetailPanel();
    }}

    function applySelectionHighlight() {{
        if (!selectedNodeId && selectedLinkIdx === null) {{
            node.classed('selected', false).classed('neighbor', false).classed('dimmed', false);
            link.classed('link-selected', false).classed('link-neighbor', false).classed('dimmed', false);
            link.attr('stroke-width', d => d.type === 'relation' ? 2 : 1.5);
            return;
        }}
        if (selectedNodeId) {{
            const neighborIds = new Set();
            const neighborLinkIdxs = new Set();
            (adjacency[selectedNodeId] || []).forEach(a => {{
                neighborIds.add(a.neighbor);
                neighborLinkIdxs.add(a.linkIdx);
            }});
            node.classed('selected', d => d.id === selectedNodeId);
            node.classed('neighbor', d => d.id !== selectedNodeId && neighborIds.has(d.id));
            node.classed('dimmed', d => d.id !== selectedNodeId && !neighborIds.has(d.id));
            node.select('text').style('display', d =>
                d.id === selectedNodeId || neighborIds.has(d.id) ? 'block' : 'none');
            link.classed('link-neighbor', (d, i) => neighborLinkIdxs.has(i));
            link.classed('dimmed', (d, i) => !neighborLinkIdxs.has(i));
            link.attr('stroke-width', (d, i) => neighborLinkIdxs.has(i) ? 3 : (d.type === 'relation' ? 2 : 1.5));
        }}
        if (selectedLinkIdx !== null) {{
            const selLink = graphData.links[selectedLinkIdx];
            const sId = typeof selLink.source === 'object' ? selLink.source.id : selLink.source;
            const tId = typeof selLink.target === 'object' ? selLink.target.id : selLink.target;
            const endpointIds = new Set([sId, tId]);
            node.classed('selected', false);
            node.classed('neighbor', d => endpointIds.has(d.id));
            node.classed('dimmed', d => !endpointIds.has(d.id));
            node.select('text').style('display', d => endpointIds.has(d.id) ? 'block' : 'none');
            link.classed('link-selected', (d, i) => i === selectedLinkIdx);
            link.classed('link-neighbor', false);
            link.classed('dimmed', (d, i) => i !== selectedLinkIdx);
            link.attr('stroke-width', (d, i) => i === selectedLinkIdx ? 4 : (d.type === 'relation' ? 2 : 1.5));
        }}
    }}

    function showDetailPanel() {{
        document.getElementById('detailPanel').classList.add('visible');
    }}
    function hideDetailPanel() {{
        document.getElementById('detailPanel').classList.remove('visible');
    }}

    function esc(s) {{
        const d = document.createElement('div');
        d.textContent = String(s ?? '');
        return d.innerHTML;
    }}

    function fmtTs(ts) {{
        if (!ts) return '-';
        try {{
            const d = new Date(typeof ts === 'number' ? ts * 1000 : ts);
            if (isNaN(d.getTime())) return String(ts);
            return d.toLocaleString('zh-CN');
        }} catch(e) {{ return String(ts); }}
    }}

    function truncate(s, n) {{
        s = String(s || '');
        return s.length > n ? s.slice(0, n - 1) + '\\u2026' : s;
    }}

    function tryParseJson(s) {{
        if (!s || typeof s !== 'string') return null;
        try {{ return JSON.parse(s); }} catch(e) {{ return null; }}
    }}

    function renderBarHtml(value, color) {{
        if (value == null || isNaN(value)) return '';
        const pct = Math.min(100, Math.max(0, Number(value) * 100));
        return '<div class="detail-bar"><div class="detail-bar-fill" style="width:' + pct + '%;background:' + color + '"></div></div>';
    }}

    function kvHtml(key, value) {{
        return '<div class="detail-kv"><div class="detail-kv-key">' + esc(key) + '</div><div>' + esc(String(value)) + '</div></div>';
    }}

    function renderNodeDetail(nodeId) {{
        const n = nodeMap[nodeId];
        if (!n) return;
        const badge = document.getElementById('detailBadge');
        badge.className = 'detail-badge ' + n.type;
        badge.textContent = nodeTypeLabel(n.type);

        const body = document.getElementById('detailBody');
        let h = '';
        h += '<div class="detail-summary">' + esc(n.summary || n.label || n.id) + '</div>';
        h += '<div class="detail-id">' + esc(n.full_id || n.id) + '</div>';

        // Core properties
        h += '<div class="detail-section"><div class="detail-section-title">属性</div>';
        if (n.type === 'event') {{
            if (n.action) h += kvHtml('动作', n.action);
            if (n.support) h += kvHtml('支撑次数', n.support);
            if (n.status) h += kvHtml('状态', n.status);
            if (n.participants) {{
                const parsed = tryParseJson(n.participants);
                if (parsed) {{
                    const pText = Array.isArray(parsed) ? parsed.map(p => p.role || p.name || JSON.stringify(p)).join(', ') : JSON.stringify(parsed);
                    h += kvHtml('参与者', pText);
                }} else if (n.participants) {{
                    h += kvHtml('参与者', n.participants);
                }}
            }}
            if (n.causality) h += kvHtml('因果关系', n.causality);
            if (n.time_range) h += kvHtml('时间范围', n.time_range);
        }} else if (n.type === 'entity') {{
            h += kvHtml('实体类型', n.entity_type || '未知');
        }} else if (n.type === 'context') {{
            if (n.context_type) h += kvHtml('类型', n.context_type);
            if (n.subtype) h += kvHtml('子类型', n.subtype);
            if (n.status) h += kvHtml('状态', n.status);
            if (n.confidence != null) {{
                h += '<div class="detail-kv"><div class="detail-kv-key">置信度</div><div>' + Number(n.confidence).toFixed(3) + renderBarHtml(n.confidence, 'var(--context-color)') + '</div></div>';
            }}
            if (n.support_count != null) h += kvHtml('支撑次数', n.support_count);
        }}
        h += '</div>';

        // Timestamps
        const timeFields = [];
        if (n.type === 'event') {{
            if (n.timestamp) timeFields.push(['创建时间', fmtTs(n.timestamp)]);
            if (n.last_active) timeFields.push(['最近活跃', fmtTs(n.last_active)]);
        }} else if (n.type === 'context') {{
            if (n.created_at) timeFields.push(['创建时间', fmtTs(n.created_at)]);
            if (n.last_seen_at) timeFields.push(['最近出现', fmtTs(n.last_seen_at)]);
        }}
        if (timeFields.length) {{
            h += '<div class="detail-section"><div class="detail-section-title">时间线</div>';
            timeFields.forEach(([k, v]) => {{ h += kvHtml(k, v); }});
            h += '</div>';
        }}

        // Structured data
        if (n.type === 'event' && n.payload) {{
            const parsed = tryParseJson(n.payload);
            if (parsed) {{
                h += '<div class="detail-section"><div class="detail-section-title">结构化事件数据</div>';
                h += '<div class="detail-json">' + esc(JSON.stringify(parsed, null, 2)) + '</div></div>';
            }}
        }}
        if (n.type === 'context' && n.description) {{
            h += '<div class="detail-section"><div class="detail-section-title">描述</div>';
            h += '<div style="font-size:13px;line-height:1.6;color:var(--text);">' + esc(n.description) + '</div></div>';
        }}

        // Connections
        const adj = adjacency[nodeId] || [];
        if (adj.length) {{
            h += '<div class="detail-section"><div class="detail-section-title">关联 (' + adj.length + ')</div><div class="detail-conn">';
            adj.forEach(a => {{
                const nb = nodeMap[a.neighbor];
                if (!nb) return;
                const meta = [];
                if (a.link.type) meta.push(edgeTypeLabel(a.link.type));
                if (a.link.rel) meta.push(a.link.rel);
                if (a.link.confidence != null) meta.push('置信度=' + Number(a.link.confidence).toFixed(2));
                h += '<div class="detail-conn-item" onclick="selectNode(\\''+esc(a.neighbor)+'\\')"><div class="detail-conn-dot '+nb.type+'"></div><div class="detail-conn-info"><div class="detail-conn-label">' + esc(truncate(nb.summary || nb.label || nb.id, 30)) + '</div><div class="detail-conn-meta">' + esc(meta.join(' · ')) + '</div></div></div>';
            }});
            h += '</div></div>';
        }}

        body.innerHTML = h;
        showDetailPanel();
    }}

    function renderLinkDetail(idx) {{
        const l = graphData.links[idx];
        if (!l) return;
        const sId = typeof l.source === 'object' ? l.source.id : l.source;
        const tId = typeof l.target === 'object' ? l.target.id : l.target;
        const sNode = nodeMap[sId];
        const tNode = nodeMap[tId];

        const badge = document.getElementById('detailBadge');
        badge.className = 'detail-badge edge-' + l.type;
        badge.textContent = edgeTypeLabel(l.type);

        const body = document.getElementById('detailBody');
        let h = '';

        if (l.type === 'relation' && l.rel) {{
            h += '<div class="detail-summary">' + esc(l.rel) + '</div>';
        }}
        if (l.description) {{
            h += '<div style="font-size:13px;line-height:1.5;color:var(--text);margin-bottom:12px;">' + esc(l.description) + '</div>';
        }}

        // Endpoints
        h += '<div class="detail-section"><div class="detail-section-title">连接节点</div>';
        h += renderEndpoint(sNode, sId, '来源');
        h += '<div class="detail-edge-arrow">&#8595;</div>';
        h += renderEndpoint(tNode, tId, '目标');
        h += '</div>';

        // Properties
        const props = [];
        if (l.confidence != null) props.push(['置信度', l.confidence, true]);
        if (l.weight != null) props.push(['权重', l.weight, true]);
        if (l.signal) props.push(['信号', l.signal, false]);
        if (l.created_at) props.push(['创建时间', fmtTs(l.created_at), false]);
        if (l.last_seen_at) props.push(['最近出现', fmtTs(l.last_seen_at), false]);

        if (props.length) {{
            h += '<div class="detail-section"><div class="detail-section-title">边属性</div>';
            props.forEach(([k, v, isNum]) => {{
                if (isNum && typeof v === 'number') {{
                    const color = v >= 0.7 ? 'var(--context-color)' : v >= 0.4 ? '#FF9F1C' : 'var(--event-color)';
                    h += '<div class="detail-kv"><div class="detail-kv-key">' + esc(k) + '</div><div>' + Number(v).toFixed(3) + renderBarHtml(v, color) + '</div></div>';
                }} else {{
                    h += kvHtml(k, v);
                }}
            }});
            h += '</div>';
        }}

        body.innerHTML = h;
        showDetailPanel();
    }}

    function renderEndpoint(n, nId, roleLabel) {{
        if (!n) {{
            return '<div class="detail-endpoint"><div class="detail-conn-dot event"></div><div class="detail-conn-info"><div class="detail-endpoint-label">' + esc(roleLabel) + '</div><div class="detail-endpoint-text" style="color:var(--text-muted)">' + esc(nId) + '</div></div></div>';
        }}
        return '<div class="detail-endpoint" onclick="selectNode(\\''+esc(n.id)+'\\')"><div class="detail-conn-dot '+n.type+'"></div><div class="detail-conn-info"><div class="detail-endpoint-label">' + esc(roleLabel) + ' · ' + nodeTypeLabel(n.type) + '</div><div class="detail-endpoint-text">' + esc(truncate(n.summary || n.label || n.id, 32)) + '</div><div class="detail-conn-meta">' + esc(truncate(n.full_id || n.id, 36)) + '</div></div></div>';
    }}

    // Init colors
    applyNodeColors();
    </script>
</body>
</html>'''

        self._close_connection()
        return html_str

    def export_html(
        self,
        output_path: str | Path,
        title: str = "LiMem 图拓扑可视化",
        include_d3_local: bool = True,
        copy_d3_library: bool = True,
    ) -> str:
        """导出可视化 HTML 到文件"""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        html = self.generate_html(title=title, include_d3_local=include_d3_local)
        output_path.write_text(html, encoding="utf-8")

        if copy_d3_library and include_d3_local:
            d3_src = Path(__file__).parent / "d3.min.js"
            d3_dst = output_path.parent / "d3.min.js"
            if d3_src.exists() and not d3_dst.exists():
                shutil.copy(d3_src, d3_dst)

        return str(output_path.absolute())

    def get_stats(self) -> dict[str, Any]:
        """获取图统计信息"""
        conn = self._get_connection()
        stats: dict[str, Any] = {}

        try:
            result = conn.execute("MATCH (n) RETURN count(n)")
            if result.has_next():
                stats["total_nodes"] = result.get_next()[0]
        except Exception:
            pass

        try:
            result = conn.execute("MATCH (e:Event) RETURN e.status, count(e)")
            stats["events"] = {}
            while result.has_next():
                row = result.get_next()
                stats["events"][row[0] or "unknown"] = row[1]
        except Exception:
            pass

        try:
            result = conn.execute("MATCH (en:Entity) RETURN count(en)")
            if result.has_next():
                stats["entities"] = result.get_next()[0]
        except Exception:
            pass

        try:
            result = conn.execute("MATCH (c:Context) RETURN count(c)")
            if result.has_next():
                stats["contexts"] = result.get_next()[0]
        except Exception:
            pass

        try:
            result = conn.execute("MATCH ()-[r]->() RETURN count(r)")
            if result.has_next():
                stats["total_edges"] = result.get_next()[0]
        except Exception:
            pass

        self._close_connection()
        return stats


# 便捷函数
def visualize_graph(
    db_path: str | Path,
    output_path: str | Path,
    config: Optional[VisualizationConfig] = None,
    title: str = "LiMem 图拓扑可视化",
) -> str:
    """可视化图数据库"""
    visualizer = GraphVisualizer(db_path, config=config)
    return visualizer.export_html(output_path, title=title)


def export_graph_html(
    db_path: str | Path,
    config: Optional[VisualizationConfig] = None,
) -> str:
    """导出图可视化 HTML 字符串"""
    visualizer = GraphVisualizer(db_path, config=config)
    return visualizer.generate_html()
