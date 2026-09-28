# 记忆库

## 创建

```http
POST /v1/limem
X-API-Key: <user-api-key>
Content-Type: application/json
```

```json
{
  "name": "车载助手测试库",
  "type": "temporary",
  "source_format": "vehicle_session_v1"
}
```

- `name`：1 到 80 个字符，不要求唯一。
- `type`：`temporary` 或 `persistent`；省略时为 `temporary`。
- `source_format`：当前只接受 `vehicle_session_v1`。

临时库创建 24 小时后不可访问，创建 48 小时后物理删除。写入和检索不会延长
寿命。长期库不会自动过期。

## 响应

```json
{
  "memory_bank_id": "bank_...",
  "name": "车载助手测试库",
  "type": "temporary",
  "source_format": "vehicle_session_v1",
  "created_at": "2026-07-29T10:00:00Z",
  "expires_at": "2026-07-30T10:00:00Z",
  "deletes_at": "2026-07-31T10:00:00Z"
}
```

长期库的 `expires_at` 和 `deletes_at` 为 `null`。

## 写入 Session

`POST /v1/limem/{memory_bank_id}/sessions` 的请求体直接是一个 Session JSON，
不使用额外包装字段。服务同步完成解析、记忆抽取、embedding 和持久化后返回：

```json
{
  "status": "written",
  "memory_bank_id": "bank_...",
  "session_id": "session-0001",
  "memory_items_created": 2
}
```

同一记忆库不能重复写入相同 `session_id`。一次失败不会留下原始 Session、记忆
或 embedding。没有抽取出记忆时仍成功写入，`memory_items_created` 为 `0`。

## Entity Profile

Profile 是调用方按稳定 ID 维护的自然语言描述。写入 Profile 不要求该 ID 已经出现
在记忆库中。Profile 不参与检索和召回。当前服务不从 Session、MemoryItem 或 LLM
生成和巩固 Profile。

使用 GET 列出当前记忆库中的全部 Entity：

```http
GET /v1/limem/{memory_bank_id}/entities
X-API-Key: <user-api-key>
```

```json
{
  "entities": [
    {
      "entity_id": "entity_...",
      "name": "用户 usr-sim-01",
      "aliases": [],
      "types": ["person"],
      "has_profile": false
    }
  ]
}
```

列表包含确认 Entity 和 Episode 范围占位 Entity，按 `entity_id` 排列。响应不包含
Profile 文本。`has_profile` 表示该 Entity 是否已经写入 Profile；内容为空字符串时仍
为 `true`。

使用 PUT 创建或整体替换 Profile：

```http
PUT /v1/limem/{memory_bank_id}/entities/{entity_id}/profile
X-API-Key: <user-api-key>
Content-Type: application/json
```

```json
{
  "text": "用户长期居住在北京，偏好简洁直接的交互。"
}
```

使用同一路径的 GET 精确读取。两个接口都返回：

```json
{
  "entity_id": "entity_...",
  "text": "用户长期居住在北京，偏好简洁直接的交互。"
}
```

`text` 接受任意字符串并原样保存。写入空字符串表示 Profile 已存在但内容已清空。
GET 从未写入的 Profile 返回 `profile_not_found`。Profile 接口只允许记忆库 owner
使用，管理员没有跨库访问权限。

## 检索

```json
{
  "query": "我平时喜欢听什么？",
  "context": null,
  "limit": 3
}
```

`query` 表示用户主动请求，`context` 表示当前环境。两个字段都必须出现，但最多
一个为 `null`。`limit` 可省略，默认 `3`，范围为 `1..10`。

响应包含Agent可读Markdown。每条主张附带按query优先BM25选中的最多3份Direct Evidence完整历史背景，分别保留Evidence、Session和Raw Event来源。背景不代表实时状态；候选不足3份全部返回，无Direct Evidence只返回主张。请求和响应JSON字段不变。

以下为无Direct Evidence时的响应示例：

```json
{
  "memory_bank_id": "bank_...",
  "content": "### 相关记忆[排名1]\n- 用户喜欢播放《稻香》"
}
```

## 删除

`DELETE /v1/limem/{memory_bank_id}` 永久删除数据库和其中的原始 Session，成功
返回 `204`。临时库进入 24 小时删除等待期后，owner 仍可主动删除，但不能列出、
写入、检索或读取内容。删除不存在或不属于当前 key 的记忆库返回 `404`。
