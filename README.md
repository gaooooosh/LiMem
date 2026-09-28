# LiMem

LiMem 将完整 Session 转成可追溯的长期记忆，提供写入、检索、实体 Profile 和重复行为巩固 HTTP API。当前来源格式为 `vehicle_session_v1`。运行服务只需本仓库、Docker Compose，以及 DeepSeek 和 DashScope 凭据。

## 快速部署

```bash
git clone https://github.com/gaooooosh/LiMem.git
cd LiMem
cp .env.example .env
chmod 600 .env
# 编辑 .env：填写管理员、DeepSeek、DashScope 三把 key
mkdir -p var
docker compose up -d --build --wait
curl --fail http://127.0.0.1:8012/health
```

管理员 key 自行生成，例如 `openssl rand -hex 32`。用户 key 由管理员通过 API 签发。默认仅监听本机 `127.0.0.1:8012`。端口冲突时修改 `.env` 的 `LIMEM_PORT`。需要远程访问时配置反向代理，或主动调整 `LIMEM_BIND_HOST`。

启动后访问 [Swagger](http://127.0.0.1:8012/docs) 和 [在线使用指南](http://127.0.0.1:8012/guide)。健康检查只验证进程，不代表模型凭据和模型网络已验证。写入、非空库检索和巩固会请求外部模型。

## 首次使用

```bash
export LIMEM_URL=http://127.0.0.1:8012
# 设置管理员 key；不要把真实凭据提交到仓库
export ADMIN_KEY='<.env 中的 LIMEM_ADMIN_API_KEY>'
curl --fail-with-body "$LIMEM_URL/v1/admin/api-keys" \
  -H "X-API-Key: $ADMIN_KEY" -H 'Content-Type: application/json' \
  -d '{"name":"my-client"}'

# 上一步返回的 api_key 只展示一次，请保存
export USER_KEY='<上一步返回的 api_key>'
curl --fail-with-body "$LIMEM_URL/v1/limem" \
  -H "X-API-Key: $USER_KEY" -H 'Content-Type: application/json' \
  -d '{"name":"车辆记忆","type":"persistent","source_format":"vehicle_session_v1"}'

export BANK_ID='<上一步返回的 memory_bank_id>'
# 按车辆协议准备 session.json；请求体直接是完整 Session
curl --fail-with-body "$LIMEM_URL/v1/limem/session-formats/vehicle-session-v1/validate" \
  -H "X-API-Key: $USER_KEY" -H 'Content-Type: application/json' \
  --data-binary @session.json
curl --fail-with-body "$LIMEM_URL/v1/limem/$BANK_ID/sessions" \
  -H "X-API-Key: $USER_KEY" -H 'Content-Type: application/json' \
  --data-binary @session.json
curl --fail-with-body "$LIMEM_URL/v1/limem/$BANK_ID/retrieve" \
  -H "X-API-Key: $USER_KEY" -H 'Content-Type: application/json' \
  -d '{"query":"我平时喜欢听什么？","context":null,"limit":3}'
```

省略记忆库 `type` 会创建临时库：24 小时后不可访问，48 小时后清理。管理员不能代替用户写入记忆。重复 `session_id` 会失败。

## 文档

- 文档索引（研发仓库历史记录：`docs/README.md`；本发行仓库不附带）：全部设计、组件和接口文档。
- [部署](docs/deployment.md)：Docker、本地 Python、持久化和更新。
- [完整配置](docs/configuration.md)：每项参数、默认值和配置来源。
- [算法与服务边界](docs/architecture.md)。
- [HTTP API](docs/service/README.md)、[车辆 Session 协议](docs/service/session-formats/vehicle-session-v1.md)。
- [领域术语](CONTEXT.md)、[当前决策](memory-bank-design/design/00-decisions.md)。
- [版本来源与验证边界](docs/distribution.md)。

## 范围

保留 `src/limem` 算法与服务、TOML 配置、依赖锁和完整设计及使用文档。独立 Git 历史不含 pro-agent、测试代码、测试数据、评测脚本、实验产物、真实密钥和运行数据库。设计文档中的解释性小 case 属于算法说明。

离线 `consolidator/behavior_agent` 属于 LiMem 算法代码，尚未注册到服务。当前服务使用 `RepeatedBehaviorConsolidator`，不需要部署 Qwen。项目仍在设计验证阶段。
