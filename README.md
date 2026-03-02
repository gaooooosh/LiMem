# LiMem - Long-Term Memory System

<div align="center">

**基于 Kuzu 图数据库和 LLM 的长时记忆系统**

实现对话式 AI 的情景记忆巩固、实体检索和加权记忆衰减

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

[English](#english-documentation) | [中文文档](#中文文档)

</div>

---

## 中文文档

### 项目简介

LiMem 是一个为对话式 AI 应用（特别是车载语音助手场景）设计的**长时记忆系统**。它通过图数据库存储结构化记忆，利用 LLM 进行记忆提取和检索，实现了类人的记忆巩固和遗忘机制。

#### 核心特性

- **情景记忆处理**：将原始对话转换为结构化事件
- **记忆巩固**：通过向量相似度合并相似记忆
- **加权检索**：基于时间衰减和实体匹配的混合检索
- **图可视化**：支持 Kuzu Explorer 进行记忆图谱可视化
- **Web 界面**：提供基于 FastAPI 的交互式搜索界面和调试面板

### 系统架构

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Episode       │ -> │     Event       │ -> │    Search       │
│   (原始对话)     │    │   (结构化事件)   │    │    (检索)        │
└─────────────────┘    └─────────────────┘    └─────────────────┘
        │                      │                      │
        v                      v                      v
   提取实体和事件         向量相似度合并         四阶段检索管道
   存储到图数据库         更新关系权重           LLM 生成答案
```

**四阶段检索管道**：
1. **实体提取** (LLM)：从用户查询中提取关键实体
2. **图路径搜索** (Kuzu Cypher)：基于实体查找相关事件
3. **权重重排序**：按时间衰减和匹配度重新排序
4. **LLM 总结**：生成自然语言回答

### 环境要求

- Python >= 3.12
- uv 包管理器（推荐）或 pip

### 快速开始

#### 1. 克隆项目

```bash
git clone https://github.com/gaooooosh/LiMem.git
cd LiMem
```

#### 2. 安装依赖

使用 uv（推荐）：

```bash
# 安装 uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 创建虚拟环境并安装依赖
uv venv
source .venv/bin/activate  # Linux/macOS
# 或 .venv\Scripts\activate  # Windows

uv pip install -e .
```

或使用 pip：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

#### 3. 配置环境变量

```bash
# 复制配置模板
cp .env.example .env

# 编辑 .env 文件，填入你的 DashScope API Key
# DASHSCOPE_API_KEY=your-api-key-here
```

> **获取 API Key**：访问 [阿里云 DashScope](https://dashscope.console.aliyun.com/) 注册并获取 API Key

#### 4. 构建 Demo 记忆库

```bash
source .venv/bin/activate
python src/script/build_ltm_from_example.py
```

这将：
- 加载 `example.json` 中的测试对话数据
- 通过 LLM 提取事件和实体
- 构建记忆图谱并保存到 `./DB/demo_db.kz`
- 导出可视化查询到 `./outputs/` 目录

#### 5. 尝试搜索

**方式一：Web 界面（推荐）**

```bash
source .venv/bin/activate
python src/script/web_demo.py
```

启动后访问：
- **Web 界面**：http://localhost:8000
- **API 文档**：http://localhost:8000/docs
- **健康检查**：http://localhost:8000/api/health

自定义端口和主机：
```bash
python src/script/web_demo.py --host 127.0.0.1 --port 8080
```

**方式二：交互式命令行**

```bash
source .venv/bin/activate
python src/script/search_demo.py
```

**方式三：运行预设查询 Demo**

```bash
python src/script/search_demo.py --demo
```

**方式四：诊断数据库**

```bash
python src/script/test_search_fixes.py
```

### 脚本说明

| 脚本 | 功能 | 用法 |
|------|------|------|
| `build_ltm_from_example.py` | 从测试数据构建记忆库 | `python src/script/build_ltm_from_example.py` |
| `web_demo.py` | 启动 Web 搜索界面 | `python src/script/web_demo.py [--host HOST] [--port PORT]` |
| `search_demo.py` | 交互式搜索 / 运行 Demo | `python src/script/search_demo.py [-d/--demo]` |
| `test_search_fixes.py` | 诊断数据库状态和权重计算 | `python src/script/test_search_fixes.py` |

### Web 界面功能

Web Demo 提供了一个功能完整的搜索界面：

#### API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | Web 搜索界面 |
| `/api/search` | POST | 执行搜索查询 |
| `/api/stats` | GET | 获取数据库统计信息 |
| `/api/health` | GET | 健康检查 |
| `/docs` | GET | Swagger API 文档 |

#### 搜索请求示例

```bash
# 使用 curl 调用搜索 API
curl -X POST "http://localhost:8000/api/search" \
  -H "Content-Type: application/json" \
  -d '{"query": "我通常早上听什么音乐？", "top_k": 5}'
```

#### 响应结构

```json
{
  "query": "我通常早上听什么音乐？",
  "answer": "根据记录，您早上通常喜欢听...",
  "entities": ["早上", "音乐", "听"],
  "ranked_events": [...],
  "top_k_events": [...],
  "debug": {
    "entity_count": 3,
    "raw_event_count": 10,
    "ranked_event_count": 8,
    "top_k_count": 5
  },
  "timestamp": "2024-01-15T10:30:00"
}
```

### 代码模块说明

```
src/
├── limem/                    # 核心包
│   ├── config.py            # 配置管理（所有可调参数）
│   ├── models.py            # 数据模型定义
│   ├── ltm.py               # 记忆巩固核心逻辑
│   ├── search.py            # 检索管道实现
│   ├── db.py                # 数据库连接和 Schema
│   ├── utils.py             # 工具函数
│   ├── data.py              # 测试数据加载
│   ├── viz.py               # 图可视化导出
│   ├── web_api.py           # FastAPI Web 服务
│   ├── demo.py              # Demo 编排器
│   └── static/              # Web 静态文件
│       └── index.html       # Web 界面
├── prompts/                  # LLM 提示词模板
│   ├── extract_event_only_system.txt
│   ├── extract_event_only_user.txt
│   ├── entity_extraction_*.txt
│   └── generate_answer_*.txt
└── script/                   # 独立脚本
    ├── build_ltm_from_example.py
    ├── web_demo.py
    ├── search_demo.py
    └── test_search_fixes.py
```

#### 核心模块详解

| 模块 | 类/函数 | 说明 |
|------|---------|------|
| `ltm.py` | `ResearchLTM` | 记忆巩固：从 Episode 提取 Event，向量合并相似事件，更新关系权重 |
| `search.py` | `LTMSearcher` | 四阶段检索：实体提取 → 图搜索 → 权重重排 → LLM 生成 |
| `web_api.py` | `app` | FastAPI 应用：提供 RESTful API 和 Web 界面 |
| `models.py` | `EpisodicEventFrame`, `RankedEvent` | 事件和检索结果的数据结构 |
| `viz.py` | `MemoryVisualizer` | 导出 Cypher 查询用于 Kuzu Explorer 可视化 |
| `config.py` | - | 集中管理所有配置参数，支持环境变量覆盖 |

### 参数配置

所有参数都在 `.env` 文件中配置，详细说明见 `.env.example`。

#### API 配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `DASHSCOPE_API_KEY` | - | 阿里云 DashScope API Key（必填） |
| `GENERATION_MODEL` | qwen3-1.7b | LLM 生成模型 |
| `EMBEDDING_MODEL` | text-embedding-v2 | 向量嵌入模型 |
| `ENABLE_THINKING` | false | 是否启用扩展推理模式 |

#### 记忆巩固参数 (LTM)

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `SIMILARITY_THRESHOLD` | 0.85 | 事件相似度阈值，高于此值则合并 |
| `DECAY_RATE` | 1e-8 | 权重衰减率，控制记忆遗忘速度（半衰期约 2.2 年） |
| `EPISODE_TTL` | 3600 | 原始对话保留时间（秒） |
| `PRUNE_C_VALID_THRESHOLD` | 100 | 高频记忆提升为永久特征的阈值 |

#### 检索参数 (Search)

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `SEARCH_TOP_K` | 5 | 返回的 Top-K 事件数量 |
| `SEARCH_LAMBDA` | 1e-9 | 检索时的权重衰减系数 |
| `SEARCH_MAX_ENTITIES` | 10 | 从查询中提取的最大实体数 |
| `SEARCH_ENABLE_VECTOR_MATCH` | true | 是否启用向量语义匹配 |
| `SEARCH_VECTOR_THRESHOLD` | 0.5 | 向量匹配相似度阈值 |

#### 权重计算公式

```
weight = log(1 + c_valid) × exp(-DECAY_RATE × time_diff)
```

- `c_valid`：事件被确认的次数
- `time_diff`：距离上次激活的时间（秒）

**衰减率参考**：
- `1e-8`：半衰期 ≈ 2.2 年（推荐，适合长期记忆）
- `1e-7`：半衰期 ≈ 81 天（中短期记忆）
- `1e-6`：半衰期 ≈ 8 天（短期记忆）

### 可视化

#### 使用 Kuzu Explorer

1. 启动 Kuzu Explorer：

```bash
docker run -p 8000:8000 -v ./DB:/database -e KUZU_FILE=/database/demo_db.kz kuzudb/explorer:latest
```

2. 访问 http://localhost:8000

3. 运行 `outputs/` 目录下导出的 Cypher 查询

#### 导出内容

构建 Demo 后，`outputs/` 目录包含：

- `memory_graph.cypher`：完整记忆图谱查询
- `event_*.cypher`：单个事件的子图查询
- `search_log_*.txt`：搜索日志（如果运行过 search_demo.py）

### 数据模型

```
┌──────────┐     INVOLVES      ┌──────────┐
│  Event   │ ───────────────> │  Entity  │
│ (事件)    │   (权重、时间戳)   │  (实体)   │
└──────────┘                  └──────────┘
     │
     │ EXTRACTED_FROM
     v
┌──────────┐                  ┌──────────┐
│ Episode  │                  │   User   │
│ (原始对话) │                  │  (用户)   │
└──────────┘                  └──────────┘
                                    │
                                    │ PERMANENT_TRAIT
                                    v
                              ┌──────────┐
                              │  Event   │
                              │ (永久特征) │
                              └──────────┘
```

**节点类型**：
- `Episode`：原始对话记录（带 TTL 自动清理）
- `Event`：结构化语义事件
- `Entity`：符号节点（人物、地点、概念）
- `User`：用户节点

**关系类型**：
- `INVOLVES`：Event → Entity（带权重和时间戳）
- `EXTRACTED_FROM`：Event → Episode（溯源）
- `PERMANENT_TRAIT`：User → Event（高频记忆提升）

### 开发指南

#### 修改提示词

所有 LLM 提示词存储在 `src/prompts/` 目录，可直接编辑 `.txt` 文件。

模板变量使用 Python `.format()` 语法：
- `{episode_text}`：原始对话
- `{query}`：用户查询
- `{events_context}`：检索到的事件上下文

#### 添加新的测试数据

编辑 `example.json`，遵循以下格式：

```json
{
  "dataset_key": [
    {
      "timestamp": 1234567890,
      "user_query": "用户说的话",
      "system_response": "系统回复"
    }
  ]
}
```

#### 扩展 Web API

Web API 基于 FastAPI 构建，可在 `src/limem/web_api.py` 中扩展：

```python
@app.post("/api/custom")
async def custom_endpoint(request: CustomRequest):
    # 你的自定义逻辑
    return {"result": "..."}
```

### 常见问题

**Q: 为什么搜索结果权重都是 0？**

A: 检查 `DECAY_RATE` 和 `SEARCH_LAMBDA` 参数。如果设置过大，历史事件的权重会快速衰减为 0。推荐值：`DECAY_RATE=1e-8`（半衰期约 2.2 年）。

**Q: 如何查看数据库中的事件？**

A: 运行诊断脚本：
```bash
python src/script/test_search_fixes.py
```

**Q: 如何重置记忆库？**

A: 删除数据库目录后重新构建：
```bash
rm -rf ./DB/demo_db.kz
python src/script/build_ltm_from_example.py
```

**Q: Web 服务启动失败？**

A: 确保已安装 FastAPI 和 uvicorn：
```bash
uv pip install fastapi uvicorn
```

**Q: API 调用返回 500 错误？**

A: 检查：
1. `.env` 文件中是否正确配置了 `DASHSCOPE_API_KEY`
2. 数据库是否已构建（运行 `build_ltm_from_example.py`）
3. 查看终端输出的错误日志

### 项目路线图

- [x] 核心记忆巩固和检索功能
- [x] Web API 和交互界面
- [x] 图可视化支持
- [ ] 多用户支持
- [ ] 记忆导出/导入功能
- [ ] 更多 LLM 后端支持（OpenAI、本地模型等）
- [ ] 性能优化和批量处理

### 贡献指南

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request

### 致谢

- [Kuzu](https://kuzudb.com/) - 嵌入式图数据库
- [DashScope](https://dashscope.aliyun.com/) - 阿里云 LLM 服务
- [FastAPI](https://fastapi.tiangolo.com/) - 现代 Web 框架

---

## English Documentation

### Overview

LiMem is a **Long-Term Memory (LTM) system** designed for conversational AI applications (specifically automotive/voice assistant scenarios). It implements episodic memory consolidation, entity-based retrieval, and weighted memory decay using Kuzu graph database and LLM abstraction.

### Core Features

- **Episodic Memory Processing**: Convert raw conversations into structured events
- **Memory Consolidation**: Merge similar memories via vector similarity
- **Weighted Retrieval**: Hybrid retrieval based on temporal decay and entity matching
- **Graph Visualization**: Support for Kuzu Explorer memory graph visualization
- **Web Interface**: Interactive search interface with debug panel based on FastAPI

### Quick Start

#### 1. Installation

```bash
git clone https://github.com/gaooooosh/LiMem.git
cd LiMem

# Using uv (recommended)
uv venv
source .venv/bin/activate
uv pip install -e .

# Or using pip
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

#### 2. Configuration

```bash
cp .env.example .env
# Edit .env and add your DASHSCOPE_API_KEY
```

> **Get API Key**: Visit [Alibaba Cloud DashScope](https://dashscope.console.aliyun.com/) to register and obtain an API Key

#### 3. Build Memory from Demo Data

```bash
source .venv/bin/activate
python src/script/build_ltm_from_example.py
```

#### 4. Search Your Memory

**Option 1: Web Interface (Recommended)**

```bash
python src/script/web_demo.py
```

After starting, visit:
- **Web Interface**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs
- **Health Check**: http://localhost:8000/api/health

Custom port and host:
```bash
python src/script/web_demo.py --host 127.0.0.1 --port 8080
```

**Option 2: Interactive CLI**

```bash
python src/script/search_demo.py
```

**Option 3: Demo Mode (Preset Queries)**

```bash
python src/script/search_demo.py --demo
```

**Option 4: Diagnose Database**

```bash
python src/script/test_search_fixes.py
```

### Scripts Reference

| Script | Description | Usage |
|--------|-------------|-------|
| `build_ltm_from_example.py` | Build memory from test data | `python src/script/build_ltm_from_example.py` |
| `web_demo.py` | Start Web search interface | `python src/script/web_demo.py [--host HOST] [--port PORT]` |
| `search_demo.py` | Interactive search / Run demo | `python src/script/search_demo.py [-d/--demo]` |
| `test_search_fixes.py` | Diagnose database and weight calculation | `python src/script/test_search_fixes.py` |

### Web Interface Features

#### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Web search interface |
| `/api/search` | POST | Execute search query |
| `/api/stats` | GET | Get database statistics |
| `/api/health` | GET | Health check |
| `/docs` | GET | Swagger API documentation |

#### Search Request Example

```bash
curl -X POST "http://localhost:8000/api/search" \
  -H "Content-Type: application/json" \
  -d '{"query": "What music do I usually listen to in the morning?", "top_k": 5}'
```

#### Response Structure

```json
{
  "query": "What music do I usually listen to in the morning?",
  "answer": "Based on records, you usually like to listen to...",
  "entities": ["morning", "music", "listen"],
  "ranked_events": [...],
  "top_k_events": [...],
  "debug": {
    "entity_count": 3,
    "raw_event_count": 10,
    "ranked_event_count": 8,
    "top_k_count": 5
  },
  "timestamp": "2024-01-15T10:30:00"
}
```

### Module Reference

| Module | Class | Description |
|--------|-------|-------------|
| `ltm.py` | `ResearchLTM` | Memory consolidation: Episode → Event extraction, vector merging, weight updates |
| `search.py` | `LTMSearcher` | 4-stage retrieval: Entity extraction → Graph search → Reranking → LLM generation |
| `web_api.py` | `app` | FastAPI application: RESTful API and Web interface |
| `models.py` | `EpisodicEventFrame`, `RankedEvent` | Data structures for events and search results |
| `viz.py` | `MemoryVisualizer` | Export Cypher queries for Kuzu Explorer visualization |
| `config.py` | - | Centralized configuration with environment variable overrides |

### Configuration Parameters

See `.env.example` for detailed parameter descriptions.

#### API Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `DASHSCOPE_API_KEY` | - | Alibaba Cloud DashScope API Key (required) |
| `GENERATION_MODEL` | qwen3-1.7b | LLM generation model |
| `EMBEDDING_MODEL` | text-embedding-v2 | Vector embedding model |
| `ENABLE_THINKING` | false | Enable extended reasoning mode |

#### Memory Consolidation (LTM)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `SIMILARITY_THRESHOLD` | 0.85 | Event similarity threshold for merging |
| `DECAY_RATE` | 1e-8 | Weight decay rate (half-life ~2.2 years) |
| `EPISODE_TTL` | 3600 | Episode retention time in seconds |
| `PRUNE_C_VALID_THRESHOLD` | 100 | Threshold for promoting to permanent trait |

#### Search Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `SEARCH_TOP_K` | 5 | Number of top-K events to return |
| `SEARCH_LAMBDA` | 1e-9 | Search weight decay coefficient |
| `SEARCH_MAX_ENTITIES` | 10 | Max entities to extract from query |
| `SEARCH_ENABLE_VECTOR_MATCH` | true | Enable vector semantic matching |
| `SEARCH_VECTOR_THRESHOLD` | 0.5 | Vector similarity threshold |

### Visualization

Start Kuzu Explorer:

```bash
docker run -p 8000:8000 -v ./DB:/database -e KUZU_FILE=/database/demo_db.kz kuzudb/explorer:latest
```

Then visit http://localhost:8000 and run the Cypher queries exported to `outputs/`.

### FAQ

**Q: Why are all search result weights 0?**

A: Check `DECAY_RATE` and `SEARCH_LAMBDA` parameters. If set too high, historical event weights will quickly decay to 0. Recommended: `DECAY_RATE=1e-8` (half-life ~2.2 years).

**Q: How to view events in the database?**

A: Run the diagnostic script:
```bash
python src/script/test_search_fixes.py
```

**Q: How to reset the memory store?**

A: Delete the database directory and rebuild:
```bash
rm -rf ./DB/demo_db.kz
python src/script/build_ltm_from_example.py
```

**Q: Web service fails to start?**

A: Ensure FastAPI and uvicorn are installed:
```bash
uv pip install fastapi uvicorn
```

### Roadmap

- [x] Core memory consolidation and retrieval
- [x] Web API and interactive interface
- [x] Graph visualization support
- [ ] Multi-user support
- [ ] Memory export/import functionality
- [ ] More LLM backend support (OpenAI, local models, etc.)
- [ ] Performance optimization and batch processing

### Contributing

Issues and Pull Requests are welcome!

1. Fork this repository
2. Create a feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Create a Pull Request

### License

MIT License

### Acknowledgments

- [Kuzu](https://kuzudb.com/) - Embedded graph database
- [DashScope](https://dashscope.aliyun.com/) - Alibaba Cloud LLM service
- [FastAPI](https://fastapi.tiangolo.com/) - Modern Web framework
