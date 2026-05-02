# LiMem

![LiMem project overview](docs/assets/676bfb97-434a-404d-b7f9-87432fdf1b67.png)

LiMem 是一个针对端侧环境原生设计的 Agent 记忆库。它可以接入任意持续到来的输入数据流，包括对话文本、设备事件、传感器状态、业务 JSON、工具调用记录和多源会话日志，并把这些原始观测组织成可持久化、可演化、可检索的长期记忆图。

LiMem 的默认路径为端侧在线写入优化：先 append-first 稳定落库，再在有预算时执行 bounded consolidation，完成上下文复用、事件关系整理、合并、归档和衰减。这个设计让记忆系统可以在资源受限、网络不稳定、输入格式不固定的环境中持续运行。

## Features

- 端侧原生设计：本地 Kuzu 持久化、轻量 BM25 检索、可控的 LLM 调用并发和 bounded 后台整理
- 任意输入数据流接入：文本、JSON、事件日志、工具调用、会话片段都可先归一为 `Episode`
- 面向 Agent 的记忆模型：从原始观测中抽取 `Event`，再关联 `Entity`、`Context` 和事件间关系
- LLM 驱动的 `Episode -> Event` 多事件抽取
- 上下文复用、冲突分叉、事件融合和记忆归档
- FastAPI 服务层，支持写入、检索、审计日志和图谱调试
- 本地图谱 HTML 可视化和批处理脚本
- 可替换的 DashScope/OpenAI-compatible 生成模型和 embedding 模型

## Why LiMem

多数记忆系统默认服务端中心化部署，并假设输入是规范化的对话消息。LiMem 反过来把端侧 Agent 的实际约束作为第一优先级：

- 输入流不可控：端侧 Agent 接收到的往往是混合数据，LiMem 允许先接收原始 JSON/文本，再统一展平、抽取和入图。
- 写入必须稳定：在线阶段不依赖全图重算，先追加事件，再异步或手动整理图结构。
- 记忆需要可解释：每条结构化记忆保留 `Episode` 溯源、证据片段、实体链接、上下文链接和事件关系。
- 资源需要可控：本地数据库、轻量索引、批量抽取、并发限制和 consolidation 开关都可以按设备能力调整。
- 业务域需要可迁移：核心库不绑定车载、助手或某个固定 schema；领域适配应通过输入数据和 prompt/config 完成。

## Requirements

- Python 3.12+
- `uv`
- DashScope API Key，或兼容 OpenAI SDK 的模型服务

项目目前使用 `src/` layout，但还没有完整打包配置。运行命令时请显式设置 `PYTHONPATH=src`。

## Installation

```bash
git clone https://github.com/gaooooosh/LiMem.git
cd LiMem
uv sync
```

复制配置模板：

```bash
cp .env.example .env
```

至少配置：

```bash
DASHSCOPE_API_KEY=your_api_key
DB_PATH=./DB/demo_db.kz
```

验证导入：

```bash
PYTHONPATH=src uv run python -c "from limem import create_ltm; print(create_ltm)"
```

## Quick Start

任意输入都可以先作为观测写入。服务层会把 JSON 展平成文本，Python API 可以直接写入文本或自行构造 `Episode`。

```python
import time
from limem import create_ltm

ltm = create_ltm(db_path="./DB/demo_db.kz")

result = ltm.ingest_text(
    "用户说：导航去公司，车机回答：已开始导航。",
    timestamp=int(time.time()),
)

print(result.to_dict())
print(ltm.get_stats())
```

接入 JSON/事件流时，可以在应用层保留原始结构，再把关键字段序列化为一段观测文本，或直接通过 HTTP `/ingest` 提交 JSON：

```json
{
  "source": "device_event_stream",
  "timestamp": 1777008724,
  "payload": {
    "screen": "navigation",
    "user_intent": "drive_to_office",
    "vehicle_state": {
      "battery": "low",
      "temperature": 28
    }
  }
}
```

运行：

```bash
PYTHONPATH=src uv run python your_script.py
```

检索演化后的记忆：

```python
rows = ltm.retrieve_memories("用户最近导航去了哪里？", top_k=5)
for row in rows:
    print(row)
```

手动执行 consolidation：

```python
report = ltm.run_consolidation(dry_run=False, strategy="auto")
print(report)
```

## HTTP Service

启动本地服务：

```bash
PYTHONPATH=src uv run python -m service.main
```

默认监听 `http://127.0.0.1:8000`。

写入一条记忆：

```bash
curl -sS -X POST http://127.0.0.1:8000/ingest \
  -H 'Content-Type: application/json' \
  -d '{
    "data": {
      "source": "car_assistant",
      "payload": {
        "speaker": "driver",
        "query": "导航去公司",
        "reply": "已开始导航"
      }
    }
  }'
```

检索：

```bash
curl -sS -X POST http://127.0.0.1:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"用户最近导航去了哪里","top_k":5}'
```

常用页面和接口：

- `GET /health`
- `GET /stats`
- `POST /ingest`
- `POST /query`
- `POST /evolve`
- `POST /rebuild-index`
- `GET /graph`
- `GET /logs`

完整说明见 [docs/http-api.md](docs/http-api.md)。

## Docker

```bash
docker compose up --build
```

`docker-compose.yml` 默认把容器服务映射到宿主机 `127.0.0.1:8012`，并持久化：

- `./DB -> /app/DB`
- `./outputs -> /app/outputs`

访问：

```bash
curl http://127.0.0.1:8012/health
```

Docker 部署会同时提供服务端可视化功能：

- `http://127.0.0.1:8012/graph`: 交互式记忆图谱页面，用于查看 `Event`、`Context`、`Entity` 和事件关系。
- `http://127.0.0.1:8012/logs`: 图操作审计日志页面，用于排查写入、合并、归档和索引重建过程。
- `GET /api/graph/snapshot`: 图谱页面使用的快照接口，可直接返回当前图节点和边。
- `POST /api/graph/write`、`POST /api/graph/update`、`POST /api/graph/delete`: 面向可信调试环境的图节点写入、更新和删除接口。

`./outputs` 会挂载到容器内 `/app/outputs`，用于持久化审计日志和图谱导出结果。公开部署时建议只通过受保护的反向代理访问这些可视化和图操作接口。

## Configuration

常用环境变量：

| Variable | Default | Description |
| --- | --- | --- |
| `DASHSCOPE_API_KEY` | empty | DashScope/OpenAI-compatible API key |
| `DASHSCOPE_BASE_URL` | DashScope compatible endpoint | OpenAI-compatible base URL |
| `GENERATION_MODEL` | `qwen3-1.7b` | generation model |
| `EMBEDDING_MODEL` | `text-embedding-v2` | embedding model |
| `EMBEDDING_DIM` | `1024` | embedding vector dimension |
| `DB_PATH` | `./DB/demo_db.kz` | Python API default Kuzu database path |
| `SERVICE_DB_PATH` | `./DB/service.kz` | HTTP service database path |
| `ENABLE_DYNAMIC_EVOLUTION` | `true` | enable context/event evolution |
| `ENABLE_EVENT_RELATIONS` | `true` | enable event-event relation extraction |
| `APPEND_FIRST_MODE` | `true` | write new events before later consolidation |
| `LLM_CONCURRENCY` | `4` | parallel extraction worker count |

Most options can also be passed to `create_ltm(..., config={...})`.

## Architecture

```text
JSON/Text -> Episode -> UnifiedExtractor -> Event
                                      |-> Context drafts

Event -> Entity
Event -> Context
Event -> Event
Event -> Episode
```

Main modules:

| Path | Purpose |
| --- | --- |
| `src/limem/core/` | data models and abstract contracts |
| `src/limem/builder/` | extraction and persistence pipeline |
| `src/limem/evolution/` | context reuse, event relations, consolidation, decay |
| `src/limem/storage/` | graph store abstraction and Kuzu implementation |
| `src/limem/retrieval/` | BM25 retrieval used by the service |
| `src/service/` | FastAPI service and graph UI endpoints |
| `src/script/` | batch tools, demos, and debug utilities |
| `src/prompts/` | prompt templates loaded by runtime code |

More detail: [docs/architecture.md](docs/architecture.md).

## Scripts

Pipeline visualizer:

```bash
PYTHONPATH=src uv run python src/script/run_pipeline_demo.py
```

Trips debugger, if you have a local `trips.json`:

```bash
PYTHONPATH=src uv run python src/script/run_trips_debugger.py
```

Build from local session data, if you have `session_v1.json`:

```bash
PYTHONPATH=src uv run python src/script/build_ltm_from_sessions.py --clear-db
```

Generate graph visualization:

```bash
PYTHONPATH=src uv run python src/script/visualize_ltm.py --db ./DB/service.kz --serve
```

## Tests

```bash
PYTHONPATH=src uv run python -m unittest discover tests
```

For development notes and repository hygiene, see [docs/development.md](docs/development.md).

## Repository Layout

```text
LiMem/
├── docs/               # maintained public documentation
├── src/
│   ├── limem/          # core library
│   ├── prompts/        # prompt templates
│   ├── script/         # demos and batch tools
│   └── service/        # FastAPI service
├── tests/              # unit and integration-style tests
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── uv.lock
```

## Security Notes

- Do not commit `.env`, API keys, local databases, generated outputs, private datasets, or assistant notes.
- Graph mutation endpoints are operator/debug APIs. Add authentication or network isolation before exposing them beyond localhost or a trusted network.
- `CLAUDE.md` and `可视化指令.txt` are local assistant/operator notes and are intentionally ignored.

## License

No license file is currently included. Add a `LICENSE` file before publishing this as an open-source project.
