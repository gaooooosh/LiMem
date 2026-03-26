# -*- coding: utf-8 -*-
"""图拓扑可视化器

从 Kuzu 图数据库提取数据并生成交互式 HTML 可视化。
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import kuzu


@dataclass
class VisualizationConfig:
    """可视化配置

    用于控制图可视化的各种参数。

    Attributes:
        max_events: 最大事件节点数
        max_entities: 最大实体节点数
        max_contexts: 最大上下文节点数
        event_base_size: 事件节点基础大小
        event_size_per_support: 每单位支持度增加的大小
        entity_size: 实体节点大小
        context_size: 上下文节点大小
        event_color: 事件节点填充色
        event_stroke: 事件节点边框色
        entity_color: 实体节点填充色
        entity_stroke: 实体节点边框色
        context_color: 上下文节点填充色
        context_stroke: 上下文节点边框色
        involves_edge_color: INVOLVES 边颜色
        relation_edge_color: EVENT_RELATION 边颜色
        link_distance: 边长度
        charge_strength: 节点间斥力强度
        collision_radius: 碰撞检测半径
        width: 视图宽度
        height: 视图高度
        background_color: 背景颜色
    """

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

    # 力导向参数
    link_distance: float = 80.0
    charge_strength: float = -200.0
    collision_radius: float = 25.0

    # 视图
    width: int = 1200
    height: int = 800
    background_color: str = "#1a1a2e"


class GraphVisualizer:
    """图拓扑可视化器

    从 LiMem 图数据库提取节点和边数据，生成交互式 HTML 可视化。

    使用方式:
        visualizer = GraphVisualizer(db_path="./my_memory.kz")
        visualizer.export_html("./viz/graph.html")

        # 或使用自定义配置
        config = VisualizationConfig(max_events=50, event_color="#FF0000")
        visualizer = GraphVisualizer(db_path="./my_memory.kz", config=config)
        html = visualizer.generate_html()
    """

    def __init__(
        self,
        db_path: str | Path,
        config: Optional[VisualizationConfig] = None,
        read_only: bool = True,
    ):
        """初始化可视化器

        Args:
            db_path: Kuzu 数据库路径
            config: 可视化配置，None 则使用默认配置
            read_only: 是否以只读模式打开数据库
        """
        self.db_path = str(db_path)
        self.config = config or VisualizationConfig()
        self.read_only = read_only

        self._db: Optional[kuzu.Database] = None
        self._conn: Optional[kuzu.Connection] = None

    def _get_connection(self) -> kuzu.Connection:
        """获取数据库连接"""
        if self._conn is None:
            self._db = kuzu.Database(self.db_path, read_only=self.read_only)
            self._conn = kuzu.Connection(self._db)
        return self._conn

    def _close_connection(self) -> None:
        """关闭数据库连接"""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        if self._db is not None:
            self._db = None

    def extract_graph_data(self) -> dict[str, Any]:
        """从数据库提取图数据

        Returns:
            包含 nodes 和 links 的字典
        """
        conn = self._get_connection()
        cfg = self.config

        data: dict[str, Any] = {"nodes": [], "links": []}
        node_ids: set[str] = set()

        # 1. 提取活跃事件节点
        result = conn.execute(f"""
            MATCH (e:Event)
            WHERE e.status = 'active'
            RETURN e.id, e.summary, e.action, e.support_count, e.timestamp
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
                "type": "event",
                "label": summary[:30] + "..." if len(summary) > 30 else summary or row[2] or "Event",
                "support": row[3] or 1,
                "action": row[2] or "",
                "timestamp": row[4],
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
                "type": "entity",
                "label": nid[:20],
                "entity_type": row[1] or "UNKNOWN",
            })

        # 3. 提取上下文节点
        result = conn.execute(f"""
            MATCH (c:Context)
            WHERE c.status = 'active'
            RETURN c.id, c.summary, c.context_type
            LIMIT {cfg.max_contexts}
        """)
        while result.has_next():
            row = result.get_next()
            nid = row[0][:16] if row[0] else f"ctx_{len(node_ids)}"
            node_ids.add(nid)
            summary = row[1] or ""
            data["nodes"].append({
                "id": nid,
                "type": "context",
                "label": summary[:25] + "..." if len(summary) > 25 else summary or "Context",
                "context_type": row[2] or "context",
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

        # 5. 提取 EVENT_RELATION 边
        try:
            result = conn.execute("""
                MATCH (e1:Event)-[r:EVENT_RELATION]->(e2:Event)
                WHERE e1.status = 'active' AND e2.status = 'active'
                RETURN e1.id, e2.id, r.relation_type
            """)
            while result.has_next():
                row = result.get_next()
                src = row[0][:16] if row[0] else None
                tgt = row[1][:16] if row[1] else None
                if src in node_ids and tgt in node_ids:
                    rel_type = row[2] if len(row) > 2 and row[2] else "related"
                    data["links"].append({"source": src, "target": tgt, "type": "relation", "rel": rel_type})
        except Exception:
            pass  # EVENT_RELATION 表可能不存在

        return data

    def generate_html(
        self,
        title: str = "LiMem 图拓扑可视化",
        include_d3_local: bool = True,
    ) -> str:
        """生成可视化 HTML

        Args:
            title: 页面标题
            include_d3_local: 是否使用本地 d3.min.js (True) 或 CDN (False)

        Returns:
            完整的 HTML 字符串
        """
        data = self.extract_graph_data()
        cfg = self.config

        # D3.js 引用
        d3_src = "d3.min.js" if include_d3_local else "https://d3js.org/d3.v7.min.js"

        # 统计
        stats = {
            "events": sum(1 for n in data["nodes"] if n["type"] == "event"),
            "entities": sum(1 for n in data["nodes"] if n["type"] == "entity"),
            "contexts": sum(1 for n in data["nodes"] if n["type"] == "context"),
            "edges": len(data["links"]),
        }

        html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{title}</title>
    <script src="{d3_src}"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: {cfg.background_color};
            color: #eee;
            overflow: hidden;
        }}
        #container {{ width: 100vw; height: 100vh; }}
        .sidebar {{
            position: fixed;
            top: 10px;
            left: 10px;
            background: rgba(30,30,50,0.95);
            padding: 15px;
            border-radius: 8px;
            width: 220px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.5);
            z-index: 100;
        }}
        .sidebar h2 {{ font-size: 14px; margin-bottom: 10px; color: #4ECDC4; }}
        .legend-item {{ display: flex; align-items: center; margin: 6px 0; font-size: 12px; }}
        .legend-dot {{ width: 12px; height: 12px; border-radius: 50%; margin-right: 8px; }}
        .stats {{ margin-top: 15px; padding-top: 10px; border-top: 1px solid #333; }}
        .stat-row {{ display: flex; justify-content: space-between; font-size: 12px; margin: 4px 0; }}
        .stat-value {{ color: #4ECDC4; font-weight: bold; }}
        .node {{ cursor: pointer; }}
        .node circle {{ stroke-width: 2px; transition: all 0.2s; }}
        .node:hover circle {{ stroke-width: 4px; filter: brightness(1.3); }}
        .node text {{ font-size: 9px; fill: #fff; pointer-events: none; text-shadow: 0 0 3px #000; }}
        .link {{ stroke-opacity: 0.4; }}
        .tooltip {{
            position: fixed;
            background: rgba(20,20,40,0.95);
            border: 1px solid #4ECDC4;
            border-radius: 6px;
            padding: 10px;
            font-size: 11px;
            max-width: 250px;
            pointer-events: none;
            z-index: 1000;
            display: none;
        }}
        .tooltip .tip-title {{ color: #4ECDC4; font-weight: bold; margin-bottom: 5px; }}
        .controls {{
            position: fixed;
            bottom: 10px;
            left: 10px;
            background: rgba(30,30,50,0.95);
            padding: 10px;
            border-radius: 8px;
            z-index: 100;
        }}
        .controls button {{
            background: #4ECDC4;
            border: none;
            padding: 6px 12px;
            border-radius: 4px;
            cursor: pointer;
            margin-right: 5px;
            font-size: 11px;
        }}
        .controls button:hover {{ background: #3dbdb5; }}
    </style>
</head>
<body>
    <div class="sidebar">
        <h2>📊 {title}</h2>
        <div class="legend-item"><div class="legend-dot" style="background:{cfg.event_color}"></div>Event (事件)</div>
        <div class="legend-item"><div class="legend-dot" style="background:{cfg.entity_color}"></div>Entity (实体)</div>
        <div class="legend-item"><div class="legend-dot" style="background:{cfg.context_color}"></div>Context (上下文)</div>
        <div class="legend-item"><div class="legend-dot" style="background:{cfg.relation_edge_color}"></div>Relation (关系)</div>
        <div class="stats">
            <div class="stat-row"><span>事件节点</span><span class="stat-value">{stats['events']}</span></div>
            <div class="stat-row"><span>实体节点</span><span class="stat-value">{stats['entities']}</span></div>
            <div class="stat-row"><span>上下文节点</span><span class="stat-value">{stats['contexts']}</span></div>
            <div class="stat-row"><span>边数量</span><span class="stat-value">{stats['edges']}</span></div>
        </div>
    </div>
    <div class="controls">
        <button onclick="resetZoom()">重置视图</button>
        <button onclick="toggleLabels()">切换标签</button>
    </div>
    <div id="container"></div>
    <div class="tooltip" id="tooltip"></div>

    <script>
    const graphData = {json.dumps(data, ensure_ascii=False)};

    const width = window.innerWidth;
    const height = window.innerHeight;

    const svg = d3.select('#container').append('svg')
        .attr('width', width)
        .attr('height', height);

    const g = svg.append('g');

    const zoom = d3.zoom()
        .scaleExtent([0.1, 4])
        .on('zoom', (event) => g.attr('transform', event.transform));

    svg.call(zoom);

    const colors = {{
        event: '{cfg.event_color}',
        entity: '{cfg.entity_color}',
        context: '{cfg.context_color}'
    }};
    const strokes = {{
        event: '{cfg.event_stroke}',
        entity: '{cfg.entity_stroke}',
        context: '{cfg.context_stroke}'
    }};

    const simulation = d3.forceSimulation(graphData.nodes)
        .force('link', d3.forceLink(graphData.links).id(d => d.id).distance({cfg.link_distance}))
        .force('charge', d3.forceManyBody().strength({cfg.charge_strength}))
        .force('center', d3.forceCenter(width / 2, height / 2))
        .force('collision', d3.forceCollide().radius({cfg.collision_radius}));

    const link = g.append('g')
        .selectAll('line')
        .data(graphData.links)
        .enter().append('line')
        .attr('class', 'link')
        .attr('stroke', d => d.type === 'relation' ? '{cfg.relation_edge_color}' : '{cfg.involves_edge_color}')
        .attr('stroke-width', d => d.type === 'relation' ? 2 : 1);

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
        .attr('r', d => d.type === 'event' ? {cfg.event_base_size} + (d.support || 1) * {cfg.event_size_per_support} : d.type === 'entity' ? {cfg.entity_size} : {cfg.context_size})
        .attr('fill', d => colors[d.type] || '#999')
        .attr('stroke', d => strokes[d.type] || '#666');

    let showLabels = true;
    const labels = node.append('text')
        .attr('dy', -12)
        .attr('text-anchor', 'middle')
        .text(d => d.label || d.id);

    node.on('mouseover', function(event, d) {{
        const tooltip = document.getElementById('tooltip');
        let html = '<div class="tip-title">' + (d.label || d.id) + '</div>';
        html += '<div>Type: ' + d.type + '</div>';
        if (d.support) html += '<div>Support: ' + d.support + '</div>';
        if (d.action) html += '<div>Action: ' + d.action + '</div>';
        tooltip.innerHTML = html;
        tooltip.style.display = 'block';
        tooltip.style.left = (event.pageX + 15) + 'px';
        tooltip.style.top = (event.pageY - 10) + 'px';
    }}).on('mouseout', function() {{
        document.getElementById('tooltip').style.display = 'none';
    }});

    simulation.on('tick', () => {{
        link
            .attr('x1', d => d.source.x)
            .attr('y1', d => d.source.y)
            .attr('x2', d => d.target.x)
            .attr('y2', d => d.target.y);
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
        svg.transition().duration(500).call(zoom.transform, d3.zoomIdentity);
    }}

    function toggleLabels() {{
        showLabels = !showLabels;
        labels.style('display', showLabels ? 'block' : 'none');
    }}
    </script>
</body>
</html>'''

        self._close_connection()
        return html

    def export_html(
        self,
        output_path: str | Path,
        title: str = "LiMem 图拓扑可视化",
        include_d3_local: bool = True,
        copy_d3_library: bool = True,
    ) -> str:
        """导出可视化 HTML 到文件

        Args:
            output_path: 输出文件路径
            title: 页面标题
            include_d3_local: 是否使用本地 d3.min.js
            copy_d3_library: 是否复制 D3.js 库到输出目录

        Returns:
            输出文件的绝对路径
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        html = self.generate_html(title=title, include_d3_local=include_d3_local)
        output_path.write_text(html, encoding="utf-8")

        # 复制 D3.js 库
        if copy_d3_library and include_d3_local:
            d3_src = Path(__file__).parent / "d3.min.js"
            d3_dst = output_path.parent / "d3.min.js"
            if d3_src.exists() and not d3_dst.exists():
                import shutil
                shutil.copy(d3_src, d3_dst)

        return str(output_path.absolute())

    def get_stats(self) -> dict[str, Any]:
        """获取图统计信息

        Returns:
            包含节点和边统计的字典
        """
        conn = self._get_connection()
        stats: dict[str, Any] = {}

        # 节点统计
        try:
            result = conn.execute("MATCH (n) RETURN count(n)")
            if result.has_next():
                stats["total_nodes"] = result.get_next()[0]
        except Exception:
            pass

        # 事件统计
        try:
            result = conn.execute("MATCH (e:Event) RETURN e.status, count(e)")
            stats["events"] = {}
            while result.has_next():
                row = result.get_next()
                stats["events"][row[0] or "unknown"] = row[1]
        except Exception:
            pass

        # 实体统计
        try:
            result = conn.execute("MATCH (en:Entity) RETURN count(en)")
            if result.has_next():
                stats["entities"] = result.get_next()[0]
        except Exception:
            pass

        # 上下文统计
        try:
            result = conn.execute("MATCH (c:Context) RETURN count(c)")
            if result.has_next():
                stats["contexts"] = result.get_next()[0]
        except Exception:
            pass

        # 边统计
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
    """可视化图数据库

    便捷函数，一行代码生成可视化。

    Args:
        db_path: 数据库路径
        output_path: 输出 HTML 路径
        config: 可视化配置
        title: 页面标题

    Returns:
        输出文件路径

    Example:
        from limem.visualization import visualize_graph
        visualize_graph("./my_memory.kz", "./viz/graph.html")
    """
    visualizer = GraphVisualizer(db_path, config=config)
    return visualizer.export_html(output_path, title=title)


def export_graph_html(
    db_path: str | Path,
    config: Optional[VisualizationConfig] = None,
) -> str:
    """导出图可视化 HTML 字符串

    Args:
        db_path: 数据库路径
        config: 可视化配置

    Returns:
        HTML 字符串
    """
    visualizer = GraphVisualizer(db_path, config=config)
    return visualizer.generate_html()
