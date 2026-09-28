# MemoryStore

## 目标

`MemoryStore` 把一个完整来源 Session 及其上游抽取结果原子地写入 SQLite：

```text
原始 Session JSON + SessionInput + EpisodeInput + L2Result + embedding
  -> SourceSession + Entity + MemoryItem + Evidence + 检索索引
```

`write_complete_session()` 在同一个事务中写入来源、MemoryItem 和 Evidence，再根据
写入后的 Evidence 数量计算并严格校验 embedding。任一步失败时，该 Session 不留下
任何数据。L2 没有抽取项时仍保存 SourceSession。

本组件不调用 LLM，不重新解释自然语言，不修补输入，不重试。它保存完整原始
Session JSON，但不单独保存解析后的 Raw Event 或 Episode。它保存巩固器已经生成
并校验的 candidate 和来源片段，但不负责生成候选。它维护供检索读取的 FTS5 和
embedding 索引。

## 接口

```python
from limem.memory_store import MemoryStore

store = MemoryStore("memory.db")
result = store.write_complete_session(
    raw_session_json,
    session_input,
    episode_input,
    l2_result,
    embedding_client,
    source_format="vehicle_session_v1",
)

source = store.get_source_session(session_input.session_ref)
memory = store.get_memory_item(result.items[0].memory_item_id)
memories = store.get_memory_items([memory.id])
entities = store.list_entities()
adjacent = store.get_adjacent_memory_items([memory.subject_entity_id])
lexical_hits = store.search_lexical(query="导航", context=None, limit=20)
evidence = store.get_evidence(result.items[0].evidence_id)

direct = store.get_active_direct_memory_items(memory.subject_entity_id)
unused_evidence = store.get_unconsolidated_evidence(
    [item.id for item in direct],
    consolidator_type="repeated_behavior",
)
pending = store.list_pending_consolidation_subjects("repeated_behavior")

embeddings = store.get_embeddings([memory.id])
store.close()
```

`write_complete_session()` 返回每个 `ExtractionItem` 对应的三个稳定引用：

```text
WriteItem {
  subject_entity_id
  memory_item_id
  evidence_id
}
```

`write()` 和 `index_embeddings()` 保留给现有实验脚本分步使用。服务写入使用
`write_complete_session()`，不能把这两个实验接口拼成服务侧写入流程。

## 写入规则

- `用户 {ref}` 只在其 Evidence 引用的 Raw Event 都包含相同 `user_ref` 时绑定为确认 Entity。
- `EpisodeInput.episode_id` 必须等于 `{SessionInput.session_ref}:episode:0`。
- SourceSession 的 `session_id` 取自 `SessionInput.session_ref`，在单个数据库中唯一。
- 调用方明确传入非空 `source_format`；MemoryStore 记录 UTC `created_at`。
- `raw_session_json` 必须是非空标准 JSON 对象，并原样保留其 JSON 值。
- 其他主体和 `entity_mentions` 创建 Episode 范围的占位 Entity。
- 同一 Episode 中相同占位文本复用同一个 Entity。不同 Episode 不按名称合并。
- 与完整 relation target 精确相同的 mention 写入 `target_entity_id`，不重复写入普通 `entity_refs`。
- 没有对应 mention 的 relation target 只保存为 `target_text`。
- 完整 MemoryItem 字段严格相等时复用 MemoryItem。组件不做语义去重。
- Evidence ID 根据 `episode_id + item位置 + ExtractionItem完整内容` 生成 UUID5。
- 直接抽取的 MemoryItem 使用 `status=active`。

## 数据表

| 表 | 内容 |
|---|---|
| `source_session` | `session_id`、来源格式、完整原始 Session JSON 和写入时间 |
| `entity` | 确认 Entity 和 Episode 范围占位 Entity |
| `memory_item` | 主体、完整主张、条件、状态和严格内容键 |
| `memory_relation` | 按位置保存 predicate、文本 target 和可空 `target_entity_id` |
| `memory_entity_ref` | 把非完整 target 的 `entity_mentions` 绑定到 Entity |
| `evidence` | 保存来源 Session ID、`raw_event_ids` 和 `context` |
| `consolidation_support` | consolidator type、candidate、来源 Evidence 和巩固器选中的原文片段 |
| `memory_embedding` | 按 Evidence 数量选择索引文本的 float32 embedding |
| `memory_item_fts` | 经 `jieba.lcut_for_search()` 分词的主张、条件和来源文本 |

占位 Entity 使用独立的 `scope_episode_id + scope_text` 唯一键。`name` 只负责展示，不参与占位身份判断。

`memory_relation.target_entity_id` 和 `memory_entity_ref.entity_id` 都有单列索引，供后续按 Entity 读取关联 MemoryItem。

`get_adjacent_memory_items(entity_ids)` 一次接收多个 Entity ID，批量返回这些 Entity 作为主体、完整 relation target 或普通 mention 出现的 MemoryItem。同一 MemoryItem 只返回一次，结果按 MemoryItem ID 排列。这个顺序只保证确定性，不表示相关性。空数组或没有命中的 ID 返回空数组。

`list_entities()` 暴露 Entity 的 `name`、`aliases` 和可空 `scope_text`，供 `QueryResolver` 做确定性表面词匹配。`list_memory_items()` 按 ID 返回全部 MemoryItem。`get_memory_items(ids)` 按输入 ID 首次出现的顺序返回已有记录。`get_embeddings(ids)` 也按输入 ID 排列，每个已有 ID 最多返回一条，并跳过不存在的记录。Retriever 负责把必需 embedding 缺失视为错误。

`get_source_session(session_id)` 返回严格 `SourceSession`。不存在的 ID 直接抛出
`KeyError`。重复写入同一个 `session_id` 触发 SQLite 唯一约束，不覆盖原记录。

`write_consolidation()` 在一个事务中写入普通 `status=candidate` MemoryItem、占位
Entity、`ConsolidationSupport`、FTS 和预计算 embedding。失败直接抛出异常，不留
部分结果。每个 candidate 必须至少有一条 Support，且同一 candidate 的 Support 只能
属于一个 consolidator type。Support 只保存 type、candidate ID、source Evidence ID
和片段。来源
MemoryItem、反向连接、支撑数量和待巩固 Evidence 都通过查询动态计算。

`list_pending_consolidation_subjects(consolidator_type)` 只统计确认 Canonical Subject 的
active 直接 MemoryItem。MemoryItem 仍引用已合并 Entity 时，通过
`canonical_entity_id` 计入最终 Subject。接口排除已经被该类型巩固器使用的 Evidence，
并按 Evidence 数量降序、Subject ID 升序返回。
`get_consolidation_candidate_source_memory_item_ids()`
沿 Support 和 Evidence 动态取得 candidate 对应的来源 MemoryItem，供 Retriever 追加
Support Step。

本次 schema 不迁移旧数据库。`consolidation_support` 缺少 `consolidator_type` 时，
MemoryStore 打开数据库立即明确报 schema 不兼容。

## BM25

写入 Evidence 后，MemoryStore 把 MemoryItem 的 `statement`、非空 `condition` 和
全部 Evidence `context` 按顺序拼接、分词并写入唯一一条 FTS5 文档。candidate
没有新的 Evidence；它的文档改为拼接 `statement`、`condition` 和全部 Support
片段。新增 Evidence 时在同一事务内重建直接 MemoryItem 的文档。重复写入相同
Evidence 不会创建重复全文记录。

`search_lexical(query, context, limit)` 拼接非空 query 和 context，使用相同的 jieba 搜索分词，并用 `OR` 连接去重后的词项。返回值只包含实际命中的 MemoryItem：

```text
LexicalSearchHit {
  memory_item_id
  bm25_score
}
```

`bm25_score` 是 SQLite FTS5 的原始值，通常为负数。结果按该值升序，再按 MemoryItem ID 打破平分。MemoryStore 不把未命中项补成 `0.0`；Retriever 读取全部 MemoryItem 后完成这一步。

## 实际 case

`session-0001` 的上游抽取项为：

```json
{
  "subject": "用户 usr-sim-01",
  "relations": [
    {
      "predicate": "开启导航",
      "target": "国网充电站·望京科技园"
    }
  ],
  "statement": "用户 usr-sim-01 开启了从望京科技园B座停车场前往国网充电站·望京科技园的导航，路线2.4公里，预计8分钟。",
  "condition": null,
  "entity_mentions": ["望京科技园B座停车场", "国网充电站·望京科技园"],
  "raw_event_ids": ["record-000002"],
  "context": "[record-000002] 2026-03-02T18:46:00+08:00，用户 usr-sim-01。导航状态发生变化。位置：望京科技园B座停车场。车外18℃；舱内23℃；噪声38dB；照度160lux。D挡；车速18km/h。成员1人：主驾。中控屏亮度4级；显示主页。导航已开启；路线信息：望京科技园B座停车场 -> 国网充电站·望京科技园，2.4km，预计8分钟。充电口未连接且已关闭；未充电；电量16%；续航88.2km；低电量提醒阈值20%。"
}
```

写入结果：

```text
Entity: 用户 usr-sim-01
  external_ref = usr-sim-01
  types = [Person]
  canonical_entity_id = self

Entity: session-0001:episode:0中的望京科技园B座停车场
  external_ref = null
  canonical_entity_id = null

Entity: session-0001:episode:0中的国网充电站·望京科技园
  external_ref = null
  canonical_entity_id = null

MemoryItem
  subject_entity_id -> 用户 Entity
  statement = 用户 usr-sim-01 开启了从望京科技园B座停车场前往...
  status = active

MemoryRelation[0]
  predicate = 开启导航
  target_text = 国网充电站·望京科技园
  target_entity_id -> 目的地占位 Entity

MemoryEntityRef[0]
  text = 望京科技园B座停车场
  entity_id -> 地点占位 Entity

Evidence
  raw_event_ids = [record-000002]
  context = MemoryItemExtractor 提供的完整 Context
```

MemoryStore 不判断文本是否具有 Entity 语义。它只消费上游已经选出的完整 target mention，并进行精确绑定。`24℃`、`所有车窗`等未被上游选中的 target 仍只有 `target_text`。

## Embedding

`write_complete_session()` 写入 Evidence 后按 MemoryItem 的当前完整状态生成 document
embedding。恰好一条 Evidence 时，索引文本为 `statement + condition + Evidence.context`；
没有 Evidence 或多于一条 Evidence 时，索引文本为 `statement + condition`。新增第二条
Evidence 时会用不含 Context 的文本替换旧向量。向量在同一事务中完成严格校验和
float32 编码，失败时回滚本次完整 Session 写入。

批量索引使用 EmbeddingClient 的 document 路径：

```python
store.index_embeddings(memory_item_ids, embedding_client)
embeddings = store.get_embeddings(memory_item_ids)
```

`index_embeddings()` 使用相同的 Evidence 数量规则生成 document embedding。它保持
输入 ID 首次出现的顺序，一次取得全部向量，确认
数量和 `client.settings.dimension` 后，再用一个事务保存。任一 ID 不存在、向量
数量不符或维度不符时直接失败，不写入部分结果。

`memory_embedding` 使用 `memory_item_id` 主键。模型名和维度直接取自
`client.settings`，向量使用 little-endian float32 BLOB。再次索引会替换该
MemoryItem 的旧向量。MemoryStore 不提供绕过索引文本规则的单条写入口。
