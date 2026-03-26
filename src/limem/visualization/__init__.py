# -*- coding: utf-8 -*-
"""LiMem 图可视化模块

提供长期记忆图数据库的交互式可视化功能。

使用方式:
    # 方式1: 直接使用可视化器
    from limem.visualization import GraphVisualizer
    viz = GraphVisualizer("./my_memory.kz")
    viz.export_html("./viz/graph.html")

    # 方式2: 便捷函数
    from limem.visualization import visualize_graph
    visualize_graph("./my_memory.kz", "./viz/graph.html")

    # 方式3: 从 LTM 实例调用
    from limem import create_ltm
    ltm = create_ltm("./my_memory.kz")
    ltm.visualize("./viz/graph.html")

    # 方式4: 命令行工具
    # python -m limem.visualization.scripts.visualize_ltm --db ./my_memory.kz
"""

from .graph_visualizer import (
    GraphVisualizer,
    VisualizationConfig,
    visualize_graph,
    export_graph_html,
)

__all__ = [
    "GraphVisualizer",
    "VisualizationConfig",
    "visualize_graph",
    "export_graph_html",
]
