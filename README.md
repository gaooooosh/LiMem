# LiMem

LiMem 是一个长期记忆服务：将完整 Session 转成可追溯的记忆，并通过 HTTP API 提供写入、检索、实体 Profile 和重复行为巩固能力。

当前支持车辆 Session 格式 `vehicle_session_v1`。使用 Docker Compose 即可部署，模型调用需要 DeepSeek 和 DashScope 凭据。

## 快速部署

### 1. 获取代码

```bash
git clone https://github.com/gaooooosh/LiMem.git
cd LiMem
cp .env.example .env
chmod 600 .env
```

### 2. 填写配置

编辑根目录 `.env`，填写以下三项：

| 配置项 | 用途 |
| --- | --- |
| `LIMEM_ADMIN_API_KEY` | 管理员 key，用于签发用户 key |
| `DEEPSEEK_API_KEY` | DeepSeek 模型调用凭据 |
| `DASHSCOPE_API_KEY` | DashScope embedding 调用凭据 |

管理员 key 可通过 `openssl rand -hex 32` 生成。模型与检索参数见[完整配置说明](docs/configuration.md)。

### 3. 启动服务

```bash
mkdir -p var
docker compose up -d --build --wait
curl --fail http://127.0.0.1:8012/health
```

默认地址为 `http://127.0.0.1:8012`。启动后可访问：

- [Swagger 接口文档](http://127.0.0.1:8012/docs)：查看接口并发送请求。
- [在线使用指南](http://127.0.0.1:8012/guide)：查看鉴权、记忆库和 Session 协议。

服务默认仅允许本机访问。端口冲突时修改 `.env` 中的 `LIMEM_PORT`；远程访问与其他部署方式见[部署文档](docs/deployment.md)。健康检查只验证服务进程，模型凭据与网络需通过实际写入、检索或巩固请求验证。

## 首次使用

调用流程：**签发用户 key → 创建记忆库 → 写入 Session → 检索记忆**。

### 1. 签发用户 key

使用 `.env` 中的管理员 key 签发一把用户 key：

```bash
export LIMEM_URL=http://127.0.0.1:8012
export ADMIN_KEY='<LIMEM_ADMIN_API_KEY>'

curl --fail-with-body "$LIMEM_URL/v1/admin/api-keys" \
  -H "X-API-Key: $ADMIN_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"name":"my-client"}'
```

保存响应中的 `api_key`，它只展示一次。后续创建和写入记忆库使用这把用户 key。

### 2. 创建记忆库

```bash
export USER_KEY='<上一步返回的 api_key>'

curl --fail-with-body "$LIMEM_URL/v1/limem" \
  -H "X-API-Key: $USER_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"name":"车辆记忆","type":"persistent","source_format":"vehicle_session_v1"}'
```

保存响应中的 `memory_bank_id`。这里使用 `persistent` 创建长期库；省略 `type` 会创建临时库，24 小时后不可访问，48 小时后清理。

### 3. 校验并写入 Session

按[车辆 Session 协议](docs/service/session-formats/vehicle-session-v1.md)准备 `session.json`。请求体直接使用完整 Session JSON。

先校验格式：

```bash
export BANK_ID='<上一步返回的 memory_bank_id>'

curl --fail-with-body "$LIMEM_URL/v1/limem/session-formats/vehicle-session-v1/validate" \
  -H "X-API-Key: $USER_KEY" \
  -H 'Content-Type: application/json' \
  --data-binary @session.json
```

校验通过后写入记忆库。此步骤会调用模型完成记忆抽取和 embedding：

```bash
curl --fail-with-body "$LIMEM_URL/v1/limem/$BANK_ID/sessions" \
  -H "X-API-Key: $USER_KEY" \
  -H 'Content-Type: application/json' \
  --data-binary @session.json
```

同一记忆库不能重复写入相同的 `session_id`。

### 4. 检索记忆

```bash
curl --fail-with-body "$LIMEM_URL/v1/limem/$BANK_ID/retrieve" \
  -H "X-API-Key: $USER_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"query":"我平时喜欢听什么？","context":null,"limit":3}'
```

响应中的 `content` 是可供 Agent 读取的 Markdown，包含相关记忆及其来源背景。实体 Profile 和巩固接口见 [HTTP API 文档](docs/service/README.md)。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [文档索引](docs/README.md) | 全部设计、组件和接口文档 |
| [部署](docs/deployment.md) | Docker、本地 Python、持久化和更新 |
| [完整配置](docs/configuration.md) | 配置来源、参数含义和默认值 |
| [HTTP API](docs/service/README.md) | 鉴权、记忆库、检索、Profile 和巩固接口 |
| [车辆 Session 协议](docs/service/session-formats/vehicle-session-v1.md) | 输入格式与字段约束 |
| [算法与服务边界](docs/architecture.md) | 写入、检索和巩固流程 |
| [领域术语](CONTEXT.md) | 核心概念与来源边界 |
| [当前决策](memory-bank-design/design/00-decisions.md) | 当前有效的设计判断 |
| [版本来源与验证](docs/distribution.md) | 版本来源和已完成的验证 |
