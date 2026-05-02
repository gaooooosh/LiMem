# LiMem

![LiMem project overview](docs/assets/676bfb97-434a-404d-b7f9-87432fdf1b67.png)

**让 Agent 在复杂端侧数据流中，精准召回当前情景真正相关的记忆。**

LiMem 是一个面向端侧环境设计的 Agent 长时记忆库。它可以接入任意输入数据流，包括对话文本、JSON、设备事件、传感器状态、工具调用记录和业务日志，并将这些观测转化为可持久化、可检索、可持续演化的记忆。

端侧 Agent 面对的不是干净的聊天记录，而是多源、碎片化、强上下文依赖的数据。LiMem 会把这些复杂输入沉淀为情景化记忆，并在后续对话、推理和工具调用中召回与当前场景最相关的部分。

## 可以做什么

- **把任意输入变成记忆**：对话、JSON、日志、设备事件都可以直接写入。
- **理解复杂端侧场景**：融合用户指令、设备状态、环境变化和历史行为。
- **情景相关召回**：不是简单关键词匹配，而是按当前上下文找到真正有用的记忆。
- **让记忆可追踪**：保留事件、实体、上下文和关系，不只是存一段文本。
- **让 Agent 能召回过去**：根据问题检索相关历史，辅助后续对话和决策。
- **适合端侧部署**：本地 Kuzu 持久化，支持弱网、隐私敏感和边缘设备场景。
- **自带可视化**：通过图谱页面查看记忆如何形成、连接和演化。

## 适用场景

- 个人 AI 助手：长期记住用户偏好、习惯和历史请求。
- 车载/IoT Agent：融合对话、设备状态、传感器和业务事件。
- 客服/运营助手：跨会话保留用户背景、问题进展和处理记录。
- 本地优先应用：在隐私敏感或弱网环境中运行记忆系统。

## 快速开始

要求：

- Python 3.12+
- `uv`
- DashScope API Key，或兼容 OpenAI SDK 的模型服务

安装依赖：

```bash
git clone https://github.com/gaooooosh/LiMem.git
cd LiMem
uv sync
cp .env.example .env
```

在 `.env` 中配置：

```bash
DASHSCOPE_API_KEY=your_api_key
DB_PATH=./DB/demo_db.kz
```

Python 使用示例：

```python
import time
from limem import create_ltm

ltm = create_ltm(db_path="./DB/demo_db.kz")

result = ltm.ingest_text(
    "用户说：导航去公司，车机回答：已开始导航。",
    timestamp=int(time.time()),
)

print(result.to_dict())
print(ltm.retrieve_memories("用户最近导航去了哪里？", top_k=5))
```

运行脚本时设置 `PYTHONPATH`：

```bash
PYTHONPATH=src uv run python your_script.py
```

## HTTP API

启动服务：

```bash
PYTHONPATH=src uv run python -m service.main
```

写入记忆：

```bash
curl -sS -X POST http://127.0.0.1:8000/ingest \
  -H 'Content-Type: application/json' \
  -d '{
    "data": {
      "source": "device_event_stream",
      "payload": {
        "user_intent": "drive_to_office",
        "screen": "navigation"
      }
    }
  }'
```

检索记忆：

```bash
curl -sS -X POST http://127.0.0.1:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"用户最近导航去了哪里","top_k":5}'
```

常用入口：

- `GET /health`
- `POST /ingest`
- `POST /query`
- `GET /graph`
- `GET /logs`

完整接口见 [docs/http-api.md](docs/http-api.md)。

## Docker 部署

```bash
docker compose up --build
```

默认访问：

- API: `http://127.0.0.1:8012`
- 图谱可视化: `http://127.0.0.1:8012/graph`
- 审计日志: `http://127.0.0.1:8012/logs`

默认持久化目录：

- `./DB`: Kuzu 数据库
- `./outputs`: 审计日志和导出结果

## 文档

- [Architecture](docs/architecture.md)
- [HTTP API](docs/http-api.md)
- [Development](docs/development.md)

## License

No license file is currently included. Add a `LICENSE` file before publishing this as an open-source project.
