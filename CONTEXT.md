# Memory Bank

本文件只定义当前设计使用的领域术语。

## Language

**Raw Event**:
原始 Session 中一条进入记忆处理的来源事件 Record。它保存完整来源内容、来源明确提供的时间以及 Session 内来源顺序。Session 边界 Record 只划定来源范围，不属于 Raw Event。RawDataParser 可以生成便于下游读取的确定性字段，但不能把改写结果冒充原始内容。
_Avoid_: Message, observation, Episode

**Session**:
来源明确划定并已经结束的一段完整活动数据。RawDataParser 一次读取一个 Session。车机场景中，一次 Session 覆盖从上车到下车的全部 Raw Event；固定频率回传不改变 Session 边界。
_Avoid_: Conversation, Episode, model batch

**RawDataParser**:
面向一种来源 JSON 格式的适配器。它严格校验完整 Session，记录来源 Record 总数，过滤只用于划定范围的 Session 边界 Record，并把其他 Record 转成 Raw Event。它输出 SessionInput，不过滤已经生成的 Raw Event，也不分组 Episode。
_Avoid_: L2 extractor, semantic segmenter

**EpisodeBuilder**:
读取一个 SessionInput，把全部 Raw Event 按来源顺序组织成 L2 使用的 Context 和 Action，并标记来源确认的 Required Action。当前一个 Session 生成一个 Episode。它只读取 RawDataParser 已经给出的确定性字段，不理解各来源的私有 JSON 结构。
_Avoid_: RawDataParser, semantic segmenter, L2 extractor

**Episode**:
EpisodeBuilder 输出的粗粒度原始数据窗口，也是 L2 一次抽取的输入。它由带 Raw Event 引用的 Context、可选 Action 和 Required Action 列表组成。Episode membership 不表示一次发生或一组共同 Evidence。
_Avoid_: Session, semantic event, Evidence

**Required Action**:
来源结构化字段确认、L2 必须保留的一次非系统主体动作。它固定引用 Raw Event、subject、actor 和 action code。Required Action 只决定动作必须进入至少一条 Extraction Item，不替代 LLM 生成开放语义字段。
_Avoid_: preference, inferred intent, fallback MemoryItem

**MemoryItemExtractor**:
读取一个完整 EpisodeInput，使用 LLM 找出直接支持的长期主张，并强制覆盖全部 Required Action，严格校验引用的 Raw Event。LLM 决定开放语义字段；MemoryItemExtractor 根据依据确定性生成 Context，输出 L2Result。车辆输入由来源适配代码维护 Session 内状态池，为 LLM 提供差分 Context；完整 Episode 保留用于引用校验和 Evidence。两种视图的 Event、Action 和 Required Action 相同。
_Avoid_: EpisodeBuilder, consolidation, write layer

**L2 Extraction Item**:
MemoryItemExtractor 从一个 Episode 中抽出的一次独立支撑。它描述一条候选主张、一个或多个有序关系，列出直接支持该主张的 Raw Event，并附带由这些 Event 确定性生成的 Context。相同内容在同一 Episode 中发生两次时，形成两个 Extraction Item。
_Avoid_: MemoryItem status candidate, Episode

**MemoryItem**:
L2 持久保存的一条主张。它说明主张关于谁、说了什么、包含哪些有序关系及各自目标，以及主张是否带条件。事实、行为、偏好和条件规律使用同一种结构。
_Avoid_: Raw Event, Evidence, fact row, preference row

**Entity**:
个人、地点、对象、设备或来源明确的稳定集体的身份锚。`Entity.id` 无语义、唯一且稳定。身份尚未确认的具体对象可以使用占位 Entity；一次临时组合不是 Group Entity。
_Avoid_: Mention, role, participant list

**Entity Mention**:
一次输入中指向实体的表面文本。代词、Session 角色和关系描述都可以成为当前上下文中的 Entity Mention，但不会因此成为 Entity Alias。
_Avoid_: Entity, Entity Alias

**Entity Alias**:
同一 Entity 稳定使用的其他名称。一个名称或别名可以匹配一个或多个 Entity，但不能证明两个指称是同一身份。
_Avoid_: Pronoun, Session role, relationship description

**Canonical Entity**:
身份确认后的最终 Entity。占位 Entity 尚未确认时没有 Canonical Entity；确认同一身份后归属于一个 Canonical Entity。
_Avoid_: Display name, latest mention

**Condition**:
一条主张成立或适用的自然语言硬条件。`statement` 表达主张内容，`condition` 表达其 IF 部分。重复行为巩固允许固定动作支持组中超过50%的 Evidence 共同支持 Condition；多个事实组合为 AND 时，完整条件的共同支持也须超过50%。当前按输入 Evidence 标签计数并保留重复观察，不把该计数等同于独立发生次数或条件正确率。一般环境信息保留在 Context；一次行为发生时的周围情境不自动成为 Condition。
_Avoid_: Situation snapshot, condition feature map

**Evidence**:
一次独立支撑与其原始依据之间的记录。每条 Evidence 指向一个 MemoryItem，保存来源 Session ID、共同支持这次主张的最小 Raw Event 集合，以及由这些 Raw Event 确定性生成的 Context。相同主张的多次独立支撑分别保存 Evidence，不能合并计数。Episode 只提供输入窗口，不定义 Evidence。
_Avoid_: Confidence, count, Episode

**Consolidation Support**:
一个 candidate MemoryItem 与一条来源 Evidence 的可追溯连接。它只保存 candidate ID、来源 Evidence ID 和实际使用的原文片段。它不复制完整 Evidence，不保存来源 MemoryItem ID、反向边、支撑数量或巩固运行状态。
每条连接保存产生它的 consolidator type，使不同巩固器分别计算待巩固 Evidence。
_Avoid_: Evidence copy, consolidated flag, confidence

**Entity Reference**:
MemoryItem 对 `Entity.id` 的引用。`subject_entity_id` 表示主张主要关于谁；每个 `relations[].target` 都可以引用目标实体；`entity_refs` 绑定正文、条件或 relation predicate 中除完整主体和完整目标外的其他具体名字。复合目标内部具有独立身份的实体也可以绑定。读取时先归一到 Canonical Entity。Entity Reference 不表达角色、数量或 AND/OR。
_Avoid_: Participant list, Group membership

**Resolver**:
把一种强类型输入中的引用解析为另一种强类型输出的组件接口。具体 Resolver 自行决定数据来源和解析方法；父类不持有 LLMClient、MemoryStore 或 fallback。
_Avoid_: Retriever, parser compatibility layer

**QueryResolver**:
Resolver 的确定性子类。它把自然语言 query 和 context 中明确出现的 Entity 名称、alias 或 scope text 链接到当前 Entity ID，不改写文本，不强行消解代词。
_Avoid_: Query rewriter, LLM coreference resolver

**EmbeddingClient**:
通过 DashScope 为 document 或 query 文本生成有序稠密向量的同步组件。它不决定索引文本、持久结构、相似度或召回算法。
_Avoid_: Vector store, Retriever

**Seed MemoryItem**:
初始候选召回直接找到的 MemoryItem。检索从 Seed MemoryItem 开始做邻接扩展；Seed MemoryItem 不等于最终结果。
_Avoid_: Seed Entity, query entity

**Retriever**:
读取 MemoryStore 已有的 BM25 和 embedding 索引，召回 Seed 并做两轮 Entity 邻接，输出排序后的诊断路径。它不调用 LLM，不维护索引，也不判断路径是否足以回答问题。
_Avoid_: QueryResolver, MemoryStore, answer generator

**Retrieval Step**:
Retrieval Result 中一个带排名的 MemoryItem。它说明该项是 Seed、第一轮扩展还是第二轮扩展，并保存实际选中的 Entity 连接和各路原始分数。
_Avoid_: MemoryItem, graph edge table

**Retrieval Result**:
当前检索返回的一条完整诊断路径。它包含一个或多个 Retrieval Step，完整保留本次选中的 MemoryItem、连接和排名。检索不判断路径是否足以回答问题；返回数量限制按完整路径计算。
_Avoid_: Complete Retrieval Result, final answer, MemoryItem

**Retrieval Result Formatter**:
把排序后的 Retrieval Result 确定性投影为 Agent 可直接读取的 Markdown。它保留完整主张、非空 condition 和非 active 状态，并从 MemoryStore 读取每条主张的 Direct Evidence，按 query 优先的 BM25 选最多三条，逐条附带完整 context 和来源。不改写、过滤或重新排序主张。
_Avoid_: answer generator, summarizer, Retriever

**返回背景**:
随记忆返回的已选 Evidence 的完整 context，描述该次支撑来源中的历史情境。不同 Evidence 的背景分别保留，历史情境不等于当前实时状态，也不自动成为可复用的 Condition。
_Avoid_: 当前状态快照, Condition, 回答正文

**API Key Identity**:
一把用户 API key 对应一个独立服务身份。记忆库归属于签发该 key 的身份；重新签发的新 key 不继承旧 key 的记忆库。管理员 key 不属于用户身份。
_Avoid_: Account, reusable user ID, bearer session

**Memory Bank**:
一把 API Key Identity 拥有的独立 MemoryStore 数据库。调用方创建时固定来源格式和生命周期类型。同一 Memory Bank 的写入、检索和删除串行执行；不同 Memory Bank 可以并行。
_Avoid_: User, MemoryItem collection view, shared database

**Temporary Memory Bank**:
创建 24 小时后不可访问、48 小时后物理删除的测试 Memory Bank。访问不会延长寿命。
_Avoid_: cache, soft-deleted bank

**Persistent Memory Bank**:
不会因时间自动过期的 Memory Bank。用户删除或管理员撤销所属 API key 时仍会立即物理删除。
_Avoid_: undeletable storage, archived bank

**Entity Connection**:
两个 MemoryItem 的 `subject_entity_id`、实体型 relation target 或 `entity_refs` 指向同一 Canonical Entity 时形成的候选连接。只共享 subject 不形成检索路径。包含 `entity_refs` 的连接只说明共同提及，不能证明有向关系。
_Avoid_: text similarity, co-occurrence

**Temporal Selection**:
根据 Evidence 指向的 Raw Event 来源时间，从同类独立发生中选择最近一次或指定时间范围内的发生。MemoryItem 创建时间、Evidence ID 和 `received_at` 不能替代来源发生时间。
_Avoid_: recency score, latest MemoryItem

**Compound Retrieval**:
一个 Retrieval Result 组合主张召回、Temporal Selection 或 Entity Connection 中至少两种操作。Compound Retrieval 不表示所有操作都是实体图上的跳转。
_Avoid_: entity-only multi-hop

车辆状态池内部仍按原始字段和值比较；LLM输入使用Parser的FIELD_RULES、render_leaf与render_payload_value生成自然语言。保留前后值的来源ID和时间。同名字段标签附原路径，未覆盖的字符串值保留原值；不新增映射词典、不删事件、不改Action及Required Action，完整Episode继续用于Evidence。
