# 黄金样例二：检索接口验证 Case

## 公共约束

固定请求结构：

```text
query: text | null
context: text | null
limit: int > 0
seed_limit: int > 0
neighbor_limit: int > 0
rrf_k: int >= 0
bm25_weight: float > 0
entity_match_weight: float >= 0
```

query 和 context 至少一个非空。调用方不提供 Entity ID。除单路/双路真实
消融外，契约测试使用人工 MemoryItem 和固定 lexical/embedding 排名，避免把
外部模型波动写入 oracle。

`limit` 只限制完整路径数量。路径一旦选中，不能截断内部 Step。当前不做
确定性相关性过滤，也不判断结果是否足以回答问题。

机器可读 fixture 位于 `data/test/retrieval/contract-cases.json`，manifest 标记
为 `human_annotated`。

## Embedding 消融

### A1：明确 query 不被 context 淹没

固定四条记忆：

```text
M-A1a: 用户A住在杭州
M-A1b: 用户A通常喜欢听萧敬腾；condition=下雨且正在驾驶时
M-A1c: 用户A通常先前往学校；condition=小明在车上时
M-A1d: 用户A喜欢较低的车内温度；condition=长途驾驶时
```

请求：

```text
query: 用户A住在哪里？
context: 当前下雨，用户A正在驾驶，小明在车上
```

目标：M-A1a 进入前两名。

### A2：模糊 query 使用 context

固定四条记忆：

```text
M-A2a: 用户A通常喜欢听萧敬腾；condition=下雨且正在驾驶时
M-A2b: 用户A通常先前往学校；condition=小明在车上时
M-A2c: 用户A喜欢爵士乐
M-A2d: 用户A住在杭州
```

请求：

```text
query: 按平时习惯来
context: 当前下雨，用户A正在驾驶
```

目标：M-A2a 进入前两名。

使用当前 competition rank 重跑真实 `qwen3.7-text-embedding` 后，单路命中
`1/2`，双路也命中 `1/2`。按预声明规则，持平时采用合并单路：
`query + context -> statement + condition`。

## 返回数量

### N1：可用项少于 limit

数据库只有“用户A喜欢爵士乐”和“用户A喜欢古典音乐”。请求音乐偏好，
`limit=5`。

断言：返回两个单 Step 结果，不补齐五条。两项只共享 subject，不能串成路径。

### N2：可用项等于 limit

固定三条音乐偏好，请求 `limit=3`。

断言：三条都返回，不重复，不产生额外结果。

### N3：候选多于 limit

固定四条音乐偏好，固定排名为爵士、古典、民谣、摇滚。请求 `limit=3`。

断言：明确返回前三条，不重复。第四条不返回。

### N4：不做确定性相关性过滤

数据库只有“用户A住在杭州”，请求“用户A养了什么宠物”，`limit=3`。

断言：仍返回该 MemoryItem。不能返回 null，也不能生成“未找到”主张。

### N5A：扩展路径替代 Seed

```text
M-N5a: 用户A负责接小明放学
M-N5b: 小明在星光小学上学
```

请求“用户A负责接送的孩子在哪里上学”，`limit=5`、`seed_limit=1`。

断言：唯一结果为 `[M-N5a, M-N5b]`。扩展路径替代 M-N5a 单项，不同时保留
前缀。

### N5B：limit 不截断路径

固定两条独立的“用户接孩子 -> 孩子在学校上学”路径，`seed_limit=2`、
`limit=1`。

断言：只返回排名更高的一条完整两 Step 路径。不能返回两个结果，也不能把
选中的路径截成一个 Step。

## 自然语言召回

### Q1：宽泛主题

固定三条音乐记忆和一条居住记忆，请求 `query=音乐`、`limit=3`。

断言：前三条都是音乐记忆。带 condition 的音乐记忆保留完整 condition。

### Q2：只有实体名称

固定“用户A喜欢听萧敬腾”和“用户A住在杭州”。请求 `query=萧敬腾`、
`limit=1`。

断言：返回音乐记忆。检索不把 query 改写为播放动作或新偏好。

### Q7：情境只满足部分条件

```text
statement: 用户A通常喜欢听萧敬腾
condition: 下雨且正在驾驶时
```

请求：

```text
query: 现在适合听什么？
context: 当前正在下雨
```

断言：该记忆可以返回，但 condition 必须仍是“下雨且正在驾驶时”。检索不能
声称“正在驾驶”已经成立。Step 保留合并内容的 cosine 原始分数。

## 邻接路径

### H1：真实组件集成

```text
M-H1a: 用户 usr-sim-01 负责接小明放学
M-H1b: 小明在星光小学上学
M-H1c: 星光小学位于朝阳路88号
```

三条 ExtractionItem 通过真实 MemoryStore 写入，复用同一 Episode 范围占位
Entity。随后建立持久 embedding，并用真实 QueryResolver、FTS5 和 Retriever
查询“用户 usr-sim-01 负责接送的孩子所在学校的地址”。

断言：返回顺序为 `[M-H1a, M-H1b, M-H1c]`，stage 为
`seed, expansion_1, expansion_2`。

### H3：路径缺边

数据库只有“用户A负责接小明放学”，没有学校记忆。

断言：保留单 Step Seed。不能猜测学校或创建不存在的 Step。

### H5：两轮扩展

```text
M-H5a: 用户A负责接小明放学
M-H5b: 小明在星光小学上学
M-H5c: 星光小学位于朝阳路88号
```

断言：第一轮通过 `child-a`，第二轮通过 `school-a`。完整路径包含三项；两条
`via` 都记录前一项 `relation_target[0]` 到后一项 `subject`。

### H6：entity_refs 参与扩展

```text
M-H6a: 用户A和小明一起去了欢乐谷；entity_refs[0]=小明
M-H6b: 小明喜欢听周杰伦
```

断言：第一轮通过 M-H6a 的 `entity_ref[0]` 找到 M-H6b。该连接只表示两项
引用同一 Entity，不能脱离 statement 推断同行关系。

### H8：遍历全部 relation target

```text
M-H8a.relations[0]: 关闭勿扰模式
M-H8a.relations[1]: 恢复播放歌曲 -> 《稻香》
M-H8b: 《稻香》由周杰伦演唱
```

断言：第一轮通过 `relations[1].target_entity_id` 找到 M-H8b，`via` 的
`previous_position=1`。只读取第一项 relation 的实现失败。

## 状态

固定 active、candidate、inactive 三条音乐记忆。

断言：三种状态都可以返回，MemoryItem.status 原样保留。Retriever 不过滤，
也不把 candidate 或 inactive 改写成 active。

## 暂缓 Case

以下内容没有进入本轮接口和黄金 fixture：

- predicate 精确过滤。自然语言请求没有结构化 predicate 条件，旧 H9 无真实
  调用入口。
- 通用角色条件与 Entity 关系联合判断。
- 每 Entity 邻接宽度的定量选择。
- Evidence、Raw Event 回读和时间选择。
- 跨 Session Entity 合并质量。

这些 case 后续需要单独确定输入和 oracle，不能从当前路径结果推定已经支持。
