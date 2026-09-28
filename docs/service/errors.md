# 错误处理

所有错误使用同一结构：

```json
{
  "error": {
    "code": "invalid_session_format",
    "message": "Session 格式不符合 vehicle_session_v1 要求。",
    "guidance": "请根据 details 指出的字段修改 Session，并对照 vehicle_session_v1 格式文档后重新提交。",
    "request_id": "req_...",
    "details": [],
    "docs_url": "/guide/session-formats/vehicle-session-v1"
  }
}
```

可把完整错误交给 Coding Agent。服务使用固定模板生成 `guidance`，不调用 LLM。

| HTTP | code | 含义 |
| ---: | --- | --- |
| 401 | `invalid_api_key` | 缺少、无效或已撤销的 key |
| 404 | `memory_bank_not_found` | 库不存在、已过期、已删除或不属于当前 key |
| 404 | `profile_not_found` | 指定 Entity 尚未写入 Profile |
| 404 | `api_key_not_found` | 管理员提交了未知 key_id |
| 404 | `route_not_found` | 请求路径不存在 |
| 405 | `method_not_allowed` | 路径不接受当前 HTTP 方法 |
| 409 | `duplicate_session` | 该 Session 已经成功处理 |
| 409 | `consolidation_already_running` | 该记忆库已有手动或自动巩固运行 |
| 404 | `consolidation_run_not_found` | 当前进程中不存在对应的自动巩固运行 |
| 409 | `consolidation_run_not_active` | 自动巩固运行已经结束，不能取消 |
| 422 | `invalid_request` | HTTP 请求字段不符合接口 schema |
| 422 | `invalid_session_format` | Session 不符合指定的来源格式 |
| 502 | `write_failed` | LLM 或 embedding 阶段失败 |
| 500 | `write_failed` | Episode 或持久化阶段失败 |
| 500/502 | `retrieval_failed` | 检索或 query embedding 失败 |
| 500/502 | `consolidation_failed` | 巩固在返回的 `stage` 阶段失败 |
| 500 | `memory_bank_deletion_failed` | 物理数据库删除失败 |
| 500 | `key_revocation_failed` | key 已撤销，但部分数据库删除失败 |
| 500 | `internal_error` | 未分类的服务内部错误 |

服务不自动重试失败请求。`write_failed` 明确表示本次 Session 没有写入任何数据，
调用方可以根据 `request_id` 联系管理员后决定是否重新提交。
