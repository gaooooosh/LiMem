# L2 持久化

## 目标

服务写入层完成以下路径：

```text
原始 Session JSON + SessionInput + EpisodeInput + L2Result + embedding
  -> SQLite
  -> SourceSession + Entity + MemoryItem + Evidence + 检索索引
```

`write_complete_session()` 先在事务外生成全部 MemoryItem embedding，并严格校验
数量、模型、维度、有限数值和 float32 编码。随后使用一个事务写入原始 Session、
MemoryItem、Evidence、FTS5 和 embedding。任一步失败时，整次 Session 写入回滚。
L2Result.items 为空时不调用 EmbeddingClient，但仍保存 SourceSession。

当前 EpisodeBuilder 每个 Session 只生成一个 Episode。写入时 `EpisodeInput.episode_id` 必须等于 `{SessionInput.session_ref}:episode:0`。不匹配时直接失败。

本轮保存完整原始 Session JSON，不单独保存解析后的 Raw Event 和 Episode。
MemoryStore 不调用 LLM，不实现语义去重、巩固或检索算法，并维护检索读取的
FTS5 和 embedding 索引。

## 组件

```text
MemoryStore(database_path)

write_complete_session(
  raw_session_json,
  SessionInput,
  EpisodeInput,
  L2Result,
  embedding_client,
  source_format,
) -> WriteResult
get_source_session(session_id) -> SourceSession
write(SessionInput, EpisodeInput, L2Result) -> WriteResult
get_entity(entity_id) -> Entity
list_entities() -> Entity[]
get_memory_item(memory_item_id) -> MemoryItem
get_memory_items(memory_item_ids) -> MemoryItem[]
list_memory_items() -> MemoryItem[]
get_adjacent_memory_items(entity_ids) -> MemoryItem[]
get_evidence(evidence_id) -> Evidence
get_active_direct_memory_items(subject_entity_id) -> MemoryItem[]
get_unconsolidated_evidence(memory_item_ids) -> Evidence[]
get_consolidation_supports(candidate_id) -> ConsolidationSupport[]
get_consolidation_supports_for_evidence(evidence_id) -> ConsolidationSupport[]
get_consolidation_source_memory_items(candidate_id) -> MemoryItem[]
write_consolidation(candidates, supports, embeddings, placeholder_entities, identity_confirmations)
search_lexical(query, context, limit) -> LexicalSearchHit[]
index_embeddings(memory_item_ids, embedding_client)
get_embeddings(memory_item_ids) -> MemoryEmbedding[]
```

组件使用 Python 标准库 `sqlite3`。它不定义数据库父类或备用存储实现。

`write()` 和 `index_embeddings()` 保留给现有实验脚本。服务写入只调用
`write_complete_session()`，避免原始 Session、MemoryItem 和 embedding 分步提交。

## SourceSession

```text
SourceSession {
  session_id
  source_format
  raw_session_json
  created_at
}
```

`session_id` 直接取自 `SessionInput.session_ref`，并作为 `source_session` 表主键。
一个数据库不能重复写入相同 Session ID。重复写入直接触发唯一约束，不覆盖原值。
调用方明确提供非空 `source_format`，MemoryStore 记录 UTC `created_at`。
`raw_session_json` 必须是非空标准 JSON 对象。MemoryStore 不解释来源私有字段。

## Entity 绑定

当主体严格写成 `用户 {ref}` 时，写入层读取该 ExtractionItem 引用的 Raw Event。所有 Raw Event 的 `user_ref` 必须等于 `{ref}`。一致时，写入层按 `external_ref={ref}` 复用或创建确认 Entity：

```text
name = 用户 {ref}
external_ref = {ref}
types = [Person]
canonical_entity_id = self
```

Raw Event 缺少该 `user_ref` 或值冲突时直接失败。

其他主体和 `entity_mentions` 创建 Episode 范围的占位 Entity：

```text
name = {episode_id}中的{表面文本}
external_ref = null
types = []
canonical_entity_id = null
```

同一 Episode 的相同表面文本复用。不同 Episode 不按名称合并。

数据库为占位 Entity 单独保存 `scope_episode_id` 和 `scope_text`，并对二者建立唯一约束。拼接后的 `name` 只用于显示，不能作为占位 Entity 的复用键。

MemoryStore 不判断 relation target 是否为 Entity。所有 target 原样写入 `target_text`。当最终 `entity_mentions` 中存在与完整 target 精确相同的文本时，写入层创建或复用 Episode 范围占位 Entity，并把 ID 写入该 relation 的 `target_entity_id`。该 mention 不再重复写入 `memory_entity_ref`。没有对应 mention 的 target 仍只有文本。

## MemoryItem

直接抽取的新 MemoryItem 使用 `status=active`。

以下完整字段严格相等时复用已有 MemoryItem：

```text
subject_entity_id
relations
statement
condition
entity_refs
status
```

`relations` 和 `entity_refs` 按数组位置写入关系表。写入层不判断两个不同文本是否语义相同。

## Evidence

每个 ExtractionItem 生成一条 Evidence。来源 Session ID、`raw_event_ids` 和 `context` 原样保存。

稳定键由以下内容组成：

```text
episode_id
ExtractionItem 在 L2Result.items 中的位置
ExtractionItem 的完整内容
```

Evidence ID 使用该稳定键生成 UUID5。重复处理相同输入会复用原 Evidence。相同 MemoryItem 位于同一个结果的不同位置时，仍生成两条 Evidence。

写入层确认 Evidence 引用的 Raw Event 同时存在于 `EpisodeInput` 和 `SessionInput`。它不重复校验上游已经确定的 Context 拼接、ID 顺序和实体 mention 顺序。

## SQLite 表

```text
source_session
entity
profile
memory_item
memory_relation
memory_entity_ref
evidence
consolidation_support
memory_embedding
memory_item_fts
```

`profile` 使用调用方提供的 `entity_id` 作为主键，只保存 `text`，不关联 `entity` 表。`put_profile()` 创建或整体替换当前文本。`get_profile()` 按 ID 精确读取。空字符串按原值保存，不表示删除。

`memory_item_fts` 保存经过 `jieba.lcut_for_search()` 搜索分词的 statement、
condition 和来源文本。直接 MemoryItem 使用全部 Evidence context；candidate 使用
全部 ConsolidationSupport fragments。`search_lexical()` 把非空 query 和 context
合并分词，用 `OR` 查询，返回 SQLite FTS5 原始 BM25 分数。未命中项不由 Store
补零。

`memory_embedding` 使用 `memory_item_id` 主键。它保存 statement 和非空 condition 按顺序拼接后的单条向量。向量使用 little-endian float32 BLOB。`model` 和 `dimensions` 来自 EmbeddingClient 配置。

`index_embeddings()` 为每条 MemoryItem 生成一条 document embedding。它先取得并校验全部向量，再在一个事务内保存。任一 MemoryItem 不存在、向量数量不符或维度不符时直接失败，不写入部分结果。组件不提供绕过固定索引文本的单条 embedding 写入口。

`write_complete_session()` 直接使用本次 L2Result 中每个 ExtractionItem 的 statement
和非空 condition 生成 embedding。向量准备全部发生在写事务之前。事务内产生
MemoryItem ID 后再绑定已经校验的向量，不发起第二次模型调用。

`write_consolidation()` 接收已经校验的 candidate、Support、待确认 Entity 和预计算
embedding。可选 `identity_confirmations` 提供巩固已经确认同一身份的已有 Entity ID 组，
每组首项是最终 ID。Store 要求所有成员尚未确认、无 external_ref 且组间不重复，
在同一事务中更新 canonical_entity_id；它不判断语义身份。历史引用和 Evidence 不改写。
它在一个事务内写入全部 candidate、关系、Entity 引用、Support、FTS 和
embedding。任一步失败时整批回滚。MemoryStore 不调用巩固 LLM，也不判断 fragment
语义。

`memory_relation.target_entity_id` 和 `memory_entity_ref.entity_id` 都建立单列索引。当前只建立存储读取基础，不在本组件实现召回算法。

`get_adjacent_memory_items(entity_ids)` 使用一组 Entity ID 同时查找主体边、relation target 边和普通 mention 邻接，再批量装配完整 MemoryItem。一个 MemoryItem 命中多个输入或多个角色时只返回一次。结果按 MemoryItem ID 排列，不表达相关性。状态过滤、排名和邻接扩展轮数不属于 MemoryStore。

`list_entities()` 为 QueryResolver 提供当前 Entity 的 `name`、`aliases` 和 `scope_text`。`list_memory_items()`、`get_memory_items()` 和 `get_embeddings()` 提供检索所需的批量读取。MemoryStore 不做 BM25 与 embedding 排名合并，也不做 Entity 扩展。

## 验收 case

`session-0001` 的抽取项写入后必须满足：

1. SourceSession 原样返回输入的完整 JSON，Session ID 为 `session-0001`。
2. `用户 usr-sim-01` 成为确认 Person Entity。
3. `望京科技园B座停车场` 成为 Episode 范围占位 Entity。
4. MemoryItem 指向用户 Entity，状态为 `active`。
5. `开启导航 -> 国网充电站·望京科技园` 按位置写入，目的地同时保存 `target_text` 和占位 Entity ID。
6. `memory_entity_ref` 只把停车场表面词绑定到占位 Entity，不重复写入完整目的地 target。
7. Evidence 原样保存 `record-000002` 和完整 Context。
8. FTS5 与 statement、非空 condition 合并 embedding 在同一事务内写入。
9. 空 L2Result 只保存 SourceSession，不调用 EmbeddingClient。
10. embedding 校验失败或第二个 ExtractionItem 失败时，所有表保持空。
