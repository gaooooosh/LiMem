# LiMem - Long-Term Memory System

<div align="center">

**基于 Kuzu 图数据库和 LLM 的长时记忆系统**

当前主路径采用 append-first 事件写入，并在图中增量维护 `Context`、`Pattern` 和 `NEXT` 演化关系。

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

[English](#english) | [中文](#中文)

</div>

---

## 中文

### 项目简介

LiMem 是一个面向对话式 AI 的长时记忆系统。它把原始输入写成 `Episode`，提取为结构化 `Event`，并在图数据库中进一步维护：

- `Entity`：人物、地点、概念等符号实体
- `Context`：场景、状态、约束等稳定上下文
- `Pattern`：由多个事件归纳出的行为模式
- `NEXT`：事件之间的局部时序/因果演化关系

### 当前结构

```text
Episode -> Event -> Entity
                -> Context
                -> Pattern
Event -> Event (NEXT)
```

默认写入方式：

1. 保存 `Episode`
2. 提取 `Event`
3. 以 append-first 方式写入新事件
4. 增量更新 `Context` / `Pattern` / `NEXT`
5. 检索时综合实体、上下文、模式、时间和支持度进行排序

### 环境要求

- Python >= 3.12
- `uv`（推荐）或 `pip`

### 安装

```bash
git clone https://github.com/gaooooosh/LiMem.git
cd LiMem

uv venv
source .venv/bin/activate
uv pip install -e .
```

### 配置

复制环境变量模板：

```bash
cp .env.example .env
```

常用配置项：

- `DASHSCOPE_API_KEY`
- `DB_PATH`
- `OFFLINE_MODE`
- `ENABLE_DYNAMIC_EVOLUTION`
- `APPEND_FIRST_MODE`

Context 复用门控可通过环境变量或 `create_ltm(..., config={...})` 调整，默认采用端侧友好的轻量规则，优先避免误连：

- `CONTEXT_REUSE_GATING_ENABLED`
- `CONTEXT_REUSE_CANDIDATE_LIMIT`
- `CONTEXT_REUSE_SCORE_THRESHOLD`
- `CONTEXT_REUSE_MIN_SUMMARY_OVERLAP`
- `CONTEXT_REUSE_MIN_EVIDENCE_OVERLAP`
- `CONTEXT_REUSE_REQUIRE_EVIDENCE`
- `CONTEXT_REUSE_ALLOW_CROSS_SUBTYPE`

### 快速开始

#### 1. 构建 trips 数据库

```bash
source .venv/bin/activate
python src/script/build_ltm_from_trips.py --clear-db
```

默认会：

- 读取 `trips.json`
- 按时间顺序切分为“基础阶段 + 调试阶段”
- 先构建 `./DB/dynamic_trips.kz`
- 再回放第二段数据并输出 `json + html` 调试报告到 `./outputs/`

自定义切分点和调试快照频率：

```bash
python src/script/build_ltm_from_trips.py \
  --clear-db \
  --split-ratio 0.7 \
  --debug-snapshot-every 5 \
  --snapshot-limit 10
```

#### 2. 交互式搜索

```bash
source .venv/bin/activate
python src/script/search_demo.py
```

运行预设查询：

```bash
python src/script/search_demo.py --demo
```

#### 3. 验证动态图流程

```bash
python src/script/test_dynamic_trips.py
```

#### 4. 诊断数据库状态

```bash
python src/script/test_search_fixes.py
```

#### 5. 启动交互式 trips 调试页面

```bash
PYTHONPATH=src uv run python src/script/run_trips_debugger.py
```

打开 `http://127.0.0.1:8011` 后，可以：

- 从 `trips.json` 逐条或批量选择写入
- 实时查看 Event / Context / Pattern / NEXT 图变化
- 直接执行 `merge_event` 和 `merge_context`
- 手工写入新的 event/context 节点

### 主要脚本

| 脚本 | 功能 |
|------|------|
| `src/script/build_ltm_from_trips.py` | 两段式构建 `trips.json`，并输出可视化调试报告 |
| `src/script/run_trips_debugger.py` | 启动交互式 trips 调试页面 |
| `src/script/search_demo.py` | 交互式搜索 / 运行预设查询 |
| `src/script/test_dynamic_trips.py` | 构建并验证动态演化流程 |
| `src/script/test_search_fixes.py` | 检查数据库统计和权重计算 |

### 代码结构

```text
src/
├── limem/
│   ├── config.py
│   ├── factory.py
│   ├── ltmemory_impl.py
│   ├── db.py
│   ├── utils.py
│   ├── core/
│   ├── builder/
│   ├── retriever/
│   ├── evolution/
│   ├── migration.py
│   └── storage/
├── prompts/
└── script/
    ├── build_ltm_from_trips.py
    ├── search_demo.py
    ├── test_dynamic_trips.py
    └── test_search_fixes.py
```

核心算法库的详细说明见 [`src/limem/README.md`](src/limem/README.md)。

### 核心模块

| 模块 | 说明 |
|------|------|
| `src/limem/factory.py` | 系统装配入口，提供 `create_ltm()` |
| `src/limem/ltmemory_impl.py` | 统一记忆系统接口实现 |
| `src/limem/builder/memory_builder.py` | Episode -> Event 构建流程 |
| `src/limem/retriever/memory_searcher.py` | 检索和回答生成 |
| `src/limem/evolution/dynamic_engine.py` | 动态演化和压缩检索 |
| `src/limem/storage/kuzu_store.py` | Kuzu 图存储实现 |

### 数据模型

节点：

- `Episode`
- `Event`
- `Entity`
- `Context`
- `Pattern`
- `User`

关系：

- `EXTRACTED_FROM`
- `INVOLVES`
- `IN_REL`
- `ABSTRACT_TO`
- `NEXT`
- `PERMANENT_TRAIT`

### 常见问题

**Q: 如何重置数据库？**

```bash
rm -rf ./DB/dynamic_trips.kz
python src/script/build_ltm_from_trips.py --clear-db
```

**Q: 如何切换到离线模式？**

设置 `.env` 中的 `OFFLINE_MODE=true`，或在脚本/代码里传入 `config={"offline_mode": True}`。

**Q: 现在还有旧版 `ResearchLTM` / `LTMSearcher` / Web demo 吗？**

没有。这些兼容模块已经下线，仓库只保留当前的 `create_ltm()` 主路径。

---

## English

### Overview

LiMem is a long-term memory system built on Kuzu and LLM-based extraction/retrieval. The current mainline uses append-first `Event` ingestion and incrementally maintains:

- `Entity`
- `Context`
- `Pattern`
- `NEXT` relations between events

### Quick Start

Build a DB from `trips.json`:

```bash
source .venv/bin/activate
python src/script/build_ltm_from_trips.py --clear-db
```

Build with split-phase debugging and HTML report output:

```bash
python src/script/build_ltm_from_trips.py \
  --clear-db \
  --split-ratio 0.7 \
  --debug-snapshot-every 5
```

Run the interactive debugger UI:

```bash
PYTHONPATH=src uv run python src/script/run_trips_debugger.py
```

Search interactively:

```bash
python src/script/search_demo.py
```

Run built-in demo queries:

```bash
python src/script/search_demo.py --demo
```

### Main Entry Point

Use `create_ltm()` from `src/limem/factory.py`.

```python
from limem import create_ltm

ltm = create_ltm(db_path="./DB/dynamic_trips.kz", config={"offline_mode": True})
result = ltm.search("What does the user usually do in meeting scenarios?")
```

Frontend-facing memory graph operations are exposed on the same object:

```python
event = ltm.write(
    {"summary": "用户在会议场景开启勿扰模式", "timestamp": 1773326409},
    kind="event",
)
snapshot = ltm.snapshot(limit=10, include_inactive=True)
query_bundle = ltm.query(text="会议", limit=10)
```

### Note

Legacy compatibility modules such as `ResearchLTM`, `LTMSearcher`, the old demo builder, and the old web demo have been removed.
