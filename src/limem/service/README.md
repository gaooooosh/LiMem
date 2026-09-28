# Service

本组件把现有解析、Episode、L2、存储和检索组件连接成带鉴权的 HTTP 服务。
它不改变各组件的内部语义，也不增加重试或降级路径。

## 边界

- `app.py` 定义 `/v1/admin/*`、`/v1/limem/*`、公开的 `/docs`、`/openapi.json`、
  `/guide/*` 和 `/health`。
- `auth.py` 把 `X-API-Key` 解析为管理员或一把用户 key。
- `repository.py` 保存 key hash、状态、记忆库归属和生命周期时间。
- `manager.py` 管理数据库文件、24/48 小时生命周期和每库锁。
- `pipeline.py` 连接 Session 写入和 Agent Markdown 检索主路径。
- `consolidation.py` 协调手动巩固和进程内自动巩固运行。
- `errors.py` 生成统一 HTTP 错误；`logging.py` 生成人类可读日志。
- `config.py` 读取根目录 `.env` 和 `config/service.toml`。

API key 本身就是用户身份。新 key 不继承旧 key 的库。管理员不能创建、列出或
写入用户库，但可以检索、删除已知库，并撤销 key。撤销会同步删除该 key 的所有
数据库。

`POST /v1/limem/session-formats/vehicle-session-v1/validate` 只运行严格解析并返回
Session ID 与 Record 数量。它不进入 Episode、L2、embedding 或持久化阶段。

手动巩固同步处理一个指定 Canonical Subject。自动巩固只在调用接口时运行：先统计
每个 Subject 的待处理 Evidence，过滤少于 5 条的 Subject，再按数量降序处理最多
`max_entities` 个 Subject。运行状态保存在进程内；查询和取消不占用 Bank 数据锁，取消
在模型或 embedding 返回后、以及写事务开始前检查。取消后不写当前实体的 candidate
或 Support；已经开始的单个写事务不打断。

```text
POST /v1/limem/{memory_bank_id}/consolidations
POST /v1/limem/{memory_bank_id}/auto-consolidations
GET  /v1/limem/{memory_bank_id}/auto-consolidations/{run_id}
POST /v1/limem/{memory_bank_id}/auto-consolidations/{run_id}/cancel
```

巩固接口合同和 `RepeatedBehaviorConsolidator` 的行为见
`docs/service/consolidations.md`。服务文档接口不要求 API key，业务接口仍按上表鉴权。

手动和自动巩固都只允许 Bank owner 调用。管理员不能替用户巩固。运行期间删除 Bank
返回 `409`。

## 写入

```text
严格解析 Session
  -> 构造一个 Episode
  -> 抽取 MemoryItem
  -> 生成 embedding
  -> 一个事务保存原始 Session、MemoryItem、Evidence、FTS 和 embedding
```

同一记忆库的写入、检索和删除使用同一把锁。不同记忆库使用不同锁。服务只运行
一个 Uvicorn worker。

临时库创建 24 小时后不可访问，48 小时后删除文件和元数据。长期库不自动过期。

## 配置

根目录 `.env` 必须包含：

```text
DEEPSEEK_API_KEY
DASHSCOPE_API_KEY
LIMEM_ADMIN_API_KEY
```

`config/service.toml` 保存运行路径、清理周期和当前采用的检索参数。LLM 与
embedding 参数分别继续读取 `config/llm.toml` 和 `config/embedding.toml`。

容器内监听 `8000`。Compose 映射到主机 `127.0.0.1:8012`，挂载 `.env`、
`config/` 和 `var/`。
