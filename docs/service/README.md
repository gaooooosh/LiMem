# LiMem Agent Memory Service

本地 Compose 默认地址：`http://127.0.0.1:8012`。下文中的接口路径都基于你的部署地址。

## 接口

| 方法 | 路径 | 鉴权 | 作用 |
| --- | --- | --- | --- |
| `POST` | `/v1/admin/api-keys` | 管理员 | 签发用户 API key |
| `GET` | `/v1/admin/api-keys` | 管理员 | 列出签发记录 |
| `DELETE` | `/v1/admin/api-keys/{key_id}` | 管理员 | 撤销 key 并删除其全部数据 |
| `POST` | `/v1/limem` | 用户 | 创建记忆库 |
| `GET` | `/v1/limem` | 用户 | 列出自己的有效记忆库 |
| `DELETE` | `/v1/limem/{memory_bank_id}` | 用户或管理员 | 永久删除记忆库 |
| `POST` | `/v1/limem/session-formats/vehicle-session-v1/validate` | 用户 | 只校验一个 Session JSON |
| `POST` | `/v1/limem/{memory_bank_id}/sessions` | 用户 | 写入一个完整 Session |
| `GET` | `/v1/limem/{memory_bank_id}/entities` | 用户 | 列出可维护 Profile 的实体 |
| `PUT` | `/v1/limem/{memory_bank_id}/entities/{entity_id}/profile` | 用户 | 创建或整体替换实体 Profile |
| `GET` | `/v1/limem/{memory_bank_id}/entities/{entity_id}/profile` | 用户 | 精确读取实体 Profile |
| `POST` | `/v1/limem/{memory_bank_id}/retrieve` | 用户或管理员 | 检索 Agent Markdown |
| `POST` | `/v1/limem/{memory_bank_id}/consolidations` | 用户 | 同步巩固指定实体 |
| `POST` | `/v1/limem/{memory_bank_id}/auto-consolidations` | 用户 | 显式启动异步巩固运行 |
| `GET` | `/v1/limem/{memory_bank_id}/auto-consolidations/{run_id}` | 用户 | 查询异步巩固运行 |
| `POST` | `/v1/limem/{memory_bank_id}/auto-consolidations/{run_id}/cancel` | 用户 | 请求协作式取消异步巩固运行 |
| `GET` | `/health` | 无 | 检查服务进程 |
| `GET` | `/docs` | 无 | 查看 Swagger 接口文档 |
| `GET` | `/openapi.json` | 无 | 获取 OpenAPI schema |
| `GET` | `/guide/*` | 无 | 获取 Markdown 使用文档 |

所有受保护接口只接受 `X-API-Key` 请求头。

`/docs`、`/openapi.json`、`/guide/*` 和 `/health` 不要求 API key。它们不读取记忆库。
其他接口的鉴权规则不变。

## 最短调用流程

```bash
curl -X POST http://127.0.0.1:8012/v1/limem \
  -H 'X-API-Key: <user-api-key>' \
  -H 'Content-Type: application/json' \
  -d '{"name":"车辆测试","source_format":"vehicle_session_v1"}'
```

创建响应中的 `memory_bank_id` 用于后续写入和检索。省略 `type` 时创建
24 小时有效的临时记忆库。

详细文档：

- [鉴权](/guide/authentication)
- [记忆库](/guide/memory-banks)
- [记忆巩固](/guide/consolidations)
- [车辆 Session 格式](/guide/session-formats/vehicle-session-v1)
- [错误处理](/guide/errors)

管理员在根目录 `.env` 中配置 `LIMEM_ADMIN_API_KEY`。服务复用同一文件中的
`DEEPSEEK_API_KEY` 和 `DASHSCOPE_API_KEY`。运行参数与内部检索参数读取
`config/service.toml`。
