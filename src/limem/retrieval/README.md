# Retriever

`Retriever` 接收自然语言 `query/context`，读取 MemoryStore 已有的 BM25 和
embedding 索引，返回最多两轮 Entity 邻接组成的诊断路径。它不调用 LLM，
不建立或修改索引，也不判断结果是否足以回答问题。

## 接口

```python
from limem.retrieval import RetrievalRequest, Retriever

retriever = Retriever(memory_store, query_resolver, embedding_client)
results = retriever.retrieve(
    RetrievalRequest(
        query="用户A负责接送的孩子在哪里上学？",
        context=None,
        limit=3,
        seed_limit=5,
        neighbor_limit=2,
        rrf_k=60,
        bm25_weight=3.0,
        entity_match_weight=0.1,
        evidence_support_weight=0.25,
    )
)
```

`query` 和 `context` 可以缺省一个，不能同时缺省。其余参数在实验阶段由每次
请求显式给出：

- `limit` 限制完整路径数量。
- `seed_limit` 限制文本 Seed 数量。
- `neighbor_limit` 分别限制每轮、每个 Entity 的邻接项数量，也用于每个已
  消解 Entity 的 Seed 数量。
- `rrf_k` 是 RRF 的排名平滑参数。
- `bm25_weight` 是 BM25 名次在最终 RRF 中的权重。Embedding 权重固定为 `1`。
- `entity_match_weight` 是每个不同查询 Entity 精确命中 MemoryItem 非主体 Entity
  引用时的稀疏 RRF 权重。
- `evidence_support_weight` 是至少有两条 Direct Evidence 的 MemoryItem 获得的一次
  固定 RRF 加分。两条和更多条的分数相同。

## 流程

1. 使用 `query + context` 查询 FTS5。
2. 合并非空 `query + context`，对合并的 `statement + condition` embedding 计算
   cosine 排名。查询 Entity 与 relation target 或 `entity_refs` 指向同一
   Canonical Entity 时记录精确非主体 Entity 命中数。重复 mention 和同一 Canonical
   Entity 只计一次，subject 不计分。至少两条 Direct Evidence 记为一次有上限的
   Evidence 支持。四路共同形成最终 RRF。
3. 取 Text Seed，并用 QueryResolver 的 Entity 命中补充 Entity Seed。
4. 从每个 Seed 做两轮 Entity 邻接。第二轮不沿进入当前项的 Entity 原路返回。
5. 扩展路径替代同一前缀。路径按各 Step 的 `rrf_rank` 序列排序后应用
   `limit`。
6. 对已选路径沿 ConsolidationSupport 正向追加 candidate Support Step。

Support Step 不参与排名或 `limit`，不继续 Entity expansion。candidate 直接成为 Seed
或 Entity expansion 时，不反向展开来源 MemoryItem。同一 candidate 的多个来源合并成
一个 Step，并放在最早出现的来源 Step 后；多个 candidate 按原 RRF rank 和 ID 排序。

相同原始分数使用 competition rank，共享同一名次。MemoryItem ID 只打破
展示顺序平分，不向 RRF 注入额外名次差异。

仅共享主体的两条 MemoryItem 可以分别成为 Seed，但不会只凭
`subject_entity_id` 相同而组成路径。relation target 和 `entity_refs` 都可以
提供邻接；`entity_refs` 只说明共同提及，不能证明有向关系。

## 诊断输出

Retriever 输出用于实验调试：

```text
RetrievalResult {
  steps: [{
    memory_item
    stage: seed | expansion_1 | expansion_2 | support
    seed_entity_id: EntityId | null
    via: {
      type: entity
      from_memory_item_id
      entity_id
      previous_field / previous_position
      current_field / current_position
    } | {
      type: consolidation_support
      source_memory_item_ids
    } | null
    bm25_score / bm25_rank
    embedding_score / embedding_rank
    entity_match_score
    evidence_support_score
    rrf_score / rrf_rank
  }]
}
```

同一 MemoryItem 同时命中文本 Seed 和 Entity Seed 时只保留一次，文本 Seed
优先。只命中多个 Entity Seed 时使用 QueryResolver 输出顺序中的第一个
Entity ID 作为诊断来源。

`via` 中 subject 的 position 固定为 `null`；relation target 和 entity ref 的
position 是非负数组下标。

## Agent 输出

`RetrievalResultFormatter` 把排序后的诊断结果投影为纯文本 Markdown：

```python
from limem.retrieval import RetrievalResultFormatter

content = RetrievalResultFormatter(memory_store).format(
    results, query="用户A负责接送的孩子在哪里上学？", context=None
)
```

H5 三跳 case 的实际格式：

```markdown
### 相关记忆[排名1]
- 用户A负责接小明放学
- 小明在星光小学上学
- 星光小学位于朝阳路88号
```

每个 RetrievalResult 形成一个排名分组。组内保持 Step 顺序，输出 statement、
非空 condition 和非 active 状态。condition 使用
`（适用条件：...）`；candidate 和 inactive 分别使用 `[候选]`、`[已失效]`。
不同分组保持自包含，可以重复同一 MemoryItem。空结果输出
`未检索到相关记忆。`。

Formatter读取每个MemoryItem的Direct Evidence，在该候选集合上使用jieba分词和SQLite FTS5 BM25。先比较query分数，同分再比较请求context分数，最后按Evidence ID；固定取最多3条。候选不足3条全部返回，无直接Evidence不补背景。巩固来源片段不冒充candidate的Direct Evidence。

每份背景紧随所属主张，使用“历史发生背景”标题，保留Evidence ID、来源Session、Raw Event列表和完整context。context不筛选、摘要或截断；不同发生保持分开。无关键词匹配仍按ID取top-3，不提供时间语义或相关性阈值。历史状态不自动成为当前状态或Condition。

Agent Markdown不输出请求query/context、MemoryItem/Entity ID、relations、stage/via或排名分数。Evidence选择和Formatter不调用LLM或embedding；不改变主张召回、排序、状态和condition。

## 失败行为

Retriever 要求每条 MemoryItem 都有合并内容 embedding。模型或维度必须与
EmbeddingClient 配置一致。缺失、零向量或损坏的 Canonical Entity 引用都会
直接失败。不重试，不跳过错误项，也不临时改用另一种 embedding 路由。

单路与双路消融见
`memory-bank-design/experiments/2026-07-28-retrieval-single-dual-embedding.md`。
使用当前 competition rank 重跑后，双路和单路都命中 `1/2`。按预声明规则，
持平时使用结构更小的合并单路。

Direct Evidence读取和完整背景返回已实现；Raw Event额外回读、时间选择和predicate结构化过滤尚未实现。
