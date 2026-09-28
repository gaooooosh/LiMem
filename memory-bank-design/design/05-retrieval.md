# 检索设计边界

## 本轮目标

检索接收自然语言 query 和 context，从 MemoryItem 中召回单项或 Entity 邻接
路径。当前输出保留完整打分与连接信息，用于验证召回和路径算法。

检索不回答问题，不调用LLM，不维护索引，不做时间选择或Raw Event额外回读。

## 请求

```text
RetrievalRequest {
  query: text | null
  context: text | null
  limit: int > 0
  seed_limit: int > 0
  neighbor_limit: int > 0
  rrf_k: int >= 0
  bm25_weight: float > 0
  entity_match_weight: float >= 0
  evidence_support_weight: float >= 0
}
```

query 和 context 都是自然语言，至少一个非空。调用方不提交 Entity ID、查询
类型、跳数或查询计划。

`limit` 限制完整 RetrievalResult 数量。`seed_limit` 限制 Text Seed 数量。
`neighbor_limit` 分别限制每轮、每个 Entity 的邻接项，也限制每个已消解
Entity 补充的 Seed 数量。实验阶段由请求显式给出这些参数。

## QueryResolver

Retriever 先调用 QueryResolver。它只把 query 和 context 中明确出现的 Entity
名称、alias 或占位 Entity `scope_text` 链接到当前 Entity ID。

- 重叠候选保留最长表面词。
- 同名命中保留全部 Entity ID。
- 无法确定的代词不生成 ID。
- 不改写 query 或 context。
- 每次读取 MemoryStore 当前 Entity，不缓存。

检索在 Seed 和邻接查找前归一 Canonical Entity。归一后的 Entity 同时读取仍
引用旧占位 Entity 的 MemoryItem。没有合并依据的同名 Entity 继续分别参与
召回。

## 索引输入

MemoryStore 维护两类索引：

```text
FTS5: query + context -> statement + condition + Evidence.context[]
embedding: query + context -> statement + condition
```

FTS5 使用 `jieba.lcut_for_search()`。未命中的 MemoryItem 在 Retriever 中补为
BM25 `0.0`。命中项保留 SQLite FTS5 原始负数 BM25 分数，按升序排名。

每条 MemoryItem 的 BM25 文档按 statement、非空 condition、全部 Evidence
context 的顺序拼接。每条 MemoryItem 必须有一条只使用 statement 和非空
condition 的 embedding。请求文本按非空 query、context 的顺序用换行符拼接。
Retriever 对请求和 embedding 文档计算一次 cosine。

单路与双路消融使用相同数据、BM25、RRF 和候选数。固定 A1/A2 中，单路命中
`1/2`，双路也命中 `1/2`。按预声明规则，持平时采用结构更小的单路。记录见
`experiments/2026-07-28-retrieval-single-dual-embedding.md`。

## 排名

embedding 按完整 cosine 公式排名，再与 BM25 排名使用请求中的 `rrf_k` 做
加权 RRF。BM25 使用 `bm25_weight`，embedding 权重固定为 `1`。查询已消解
Entity 与 MemoryItem 的 relation target 或 `entity_refs` 指向同一 Canonical
Entity 时，`entity_match_score` 等于不同非主体 Canonical Entity 的命中数。每个命中
增加 `entity_match_weight / (rrf_k + 1)`。重复 mention 和同一 Canonical Entity 只计
一次；只匹配 subject 或未命中时为 `0`。
有至少两条 Direct Evidence 的 MemoryItem 记 `evidence_support_score=1`，增加
`evidence_support_weight / (rrf_k + 1)`；零条或一条记 `0`。两条以上不继续累加。

原始 BM25、cosine 和最终 RRF 不直接相加。相同原始分数使用 competition rank
共享名次。MemoryItem ID 只保证同分输出顺序，不产生不同名次。

## Seed

1. 全库按最终 `rrf_rank` 取前 `seed_limit` 个 Text Seed。
2. QueryResolver 找到的每个 Canonical Entity 分别读取邻接 MemoryItem，按
   `rrf_rank` 取前 `neighbor_limit` 个 Entity Seed。
3. 两类 Seed 取并集并按 MemoryItem ID 去重。

同一 MemoryItem 同时命中文本和 Entity Seed 时记录为 Text Seed。只命中多个
Entity Seed 时，记录 QueryResolver 顺序中的第一个 Entity ID。

## 两轮邻接

每个 MemoryItem 使用三类 Entity 引用：

```text
subject_entity_id
relations[*].target_entity_id
entity_refs[*].entity_id
```

第一轮扩展 Seed。第二轮只扩展第一轮新增项，并排除刚刚用于进入该项的
Entity。每轮对当前项的每个 Canonical Entity 分别取前 `neighbor_limit` 个
邻接项。

路径内不能重复 MemoryItem。只共享 subject 的两条 MemoryItem 不组成路径。
relation target 或 entity_ref 与另一项的任一 Entity 引用相同才可以连接。
`entity_refs` 连接只表示共同提及，不能证明有向关系。

同一前缀有多个邻接项时分别形成路径。路径成功扩展后，扩展路径替代该前缀；
不同时保留同一 Seed 的短副本。没有邻接项时保留现有路径。第二轮结束后停止。

## 输出

```text
RetrievalResult {
  steps: RetrievalStep[]
}

RetrievalStep {
  memory_item
  stage: seed | expansion_1 | expansion_2
  seed_entity_id: EntityId | null
  via: RetrievalVia | null
  bm25_score / bm25_rank
  embedding_score / embedding_rank
  entity_match_score
  evidence_support_score
  rrf_score / rrf_rank
}

RetrievalVia {
  entity_id
  previous_field / previous_position
  current_field / current_position
}
```

Seed 没有 `via`。扩展 Step 必须有 `via`，并只记录实际选择的一条连接。
subject 的 position 为 null；relation target 和 entity_ref 的 position 是各自
数组下标。

路径按各 Step 的 `rrf_rank` 序列做字典序排序，再应用 `limit`。已经选中的
路径不会被 `limit` 截断。MemoryItem 的 active、candidate、inactive 状态原样
保留，不过滤、不改写。

Retriever 继续返回上述实验诊断输出。RetrievalResultFormatter 接收完整结果
列表，并生成供 LLM Agent 直接读取的纯文本 Markdown：

```markdown
### 相关记忆[排名1]
- 用户A负责接小明放学
- 小明在星光小学上学（适用条件：工作日放学时）

### 相关记忆[排名2]
- [候选] 用户A可能喜欢民谣
```

标题排名是 RetrievalResult 在最终返回列表中的一基顺序。组内保持 Step 顺序。
Formatter保留statement、非空condition和status，并读取Direct Evidence。不同结果共享同一
MemoryItem 时仍分别完整输出，保证每组可以独立阅读。空列表输出
`未检索到相关记忆。`。

Formatter在每条已选MemoryItem的Direct Evidence集合中，使用jieba与SQLite FTS5 BM25按query分数、请求context分数降序排列，同分按Evidence ID，取最多3条。每条Evidence的完整context紧随所属主张返回，并附Evidence、Session和Raw Event ID以绑定来源。无Direct Evidence不补背景，不回退到巩固片段。不同发生不合并，context不截断或改写。主张及路径顺序不变。

Agent Markdown不输出请求query/context、MemoryItem/Entity ID、relations、entity_refs、stage/via或分数。选择不调用LLM或embedding，不引入阈值、时间过滤或来源扩展。历史背景不自动成为当前状态或Condition。有效决定见00-decisions.md的Evidence选择与返回。

## 严格失败

以下情况直接失败：

- 任一 MemoryItem 的合并内容 embedding 缺失。
- embedding 模型或维度与当前 EmbeddingClient 配置不一致。
- query、context 或文档向量为零向量。
- Canonical Entity 指向不存在的 Entity 或形成循环。

Retriever 不跳过错误项，不重试，不改用其他 embedding 路由，也不临时重建索引。

## 当前 Case

人工契约数据在 `data/test/retrieval/`。当前覆盖 N1、N2、N3、N4、N5A、N5B、
Q1、Q2、Q7、H3、H5、H6、H8 和 status。H1 使用真实 MemoryStore、
QueryResolver、FTS5 和持久 embedding 做集成验证，并在同一 manifest 注册。

`readout-cases.json` 独立覆盖单项、多跳、condition、非 active 状态、跨结果
重复和空结果的精确 Markdown。

predicate 精确过滤、时间查询、Raw Event额外回读、通用角色条件、每 Entity 宽度
消融和跨 Session Entity 合并暂缓。本轮不为这些未实现 case 增加接口。
