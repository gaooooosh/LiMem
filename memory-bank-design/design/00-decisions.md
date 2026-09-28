# 记忆库当前设计

本文只汇总当前有效结果。具体结构见各设计文档。

## 场景

- 设备为 PC、机器人或车机等 Linux 端侧设备。
- 原始输入格式复杂，进入记忆库前统一文本化。
- Session 完成后可以批量调用云端 LLM，不要求实时抽取。
- 设备可以被多人共用。记忆按主体归属，不做主体间存储隔离。
- 当前目标是验证最短主路径，不设计生产运维、重试和兼容机制。

## 分层

```text
L4 画像和摘要
L3 程序与技能
L2 实体和语义主张
L1 Episode 分组
L0 Raw Event 原始记录
```

当前只闭合 L0、L1 和 L2 的最小结构。L3、L4 后续单独设计。

## L0 Raw Event

Raw Event 是不可改写的原始记录。它保存来源文本和来源明确提供的结构化信息。

来源没有提供的说话人、时间、参与者或传感含义保持未知。RawDataParser 和 L2 不能把推测写回原始内容。

## Session、RawDataParser 与 EpisodeBuilder

Session 由来源划定。车机场景的一次 Session 覆盖从上车到下车的全部数据。固定频率回传不改变 Session 边界。

RawDataParser 面向一种来源 JSON 格式。它一次读取一个已经结束的 Session，负责：

1. 严格校验来源外壳。
2. 记录来源 Record 总数。
3. 过滤只用于划定范围的 Session 边界 Record。
4. 完整保留其他来源 Record。
5. 保存来源 ID、时间和 Session 内顺序。
6. 把来源直接表达的事件、动作和设备状态转换成可以直接阅读的简短字段。

`SessionStartEvent` 和 `SessionEndEvent` 可以缺失、单独出现、重复或位于任意位置。它们必须是 Trigger Record，且 `payload` 和 `device_tree` 必须为空。Parser 校验后不为它们生成 Raw Event。`SessionInput.record_count` 保留全部来源 Record 数量；普通 Raw Event 的 `session_position` 保留原数组位置，因此允许出现位置缺口。

普通 Record 的 `chassis.powertrain.ignition` 接受 `ready` 和 `off`。缺失仍然允许，其他值直接失败。

RawDataParser 不判断偏好、意图和长期记忆价值，也不删除已经生成的 Raw Event。

EpisodeBuilder 读取 RawDataParser 输出的 SessionInput。它不删除 Raw Event，也不理解来源私有 JSON。当前把一个 Session 的全部 Raw Event 按来源顺序组织成一个 Episode，不使用 LLM、时间窗口或语义切分。Session 只有边界 Record 时，Episode 的 events 为空，MemoryItemExtractor 直接返回空结果。长 Session 如何生成多个 Episode 尚未设计。

## L1 Episode

Episode 是 L2 的粗粒度输入窗口。它不是语义事件，也不是一次 Evidence。

```text
EpisodeInput {
  episode_id
  events: [{raw_event_id, context, action?}]
  required_actions: [{raw_event_id, subject, actor, action_code}]
}
```

`context` 按固定顺序拼接时间、用户、事件和设备状态。`action` 只使用 Raw Event 的 `actor + detail + payload`，没有动作时省略。`detail` 表达动作，`payload` 表达事件发生后的状态，不表示每个字段都相对前态发生变化。`required_actions` 按 Event 顺序列出 `actor != "系统"` 且具有 `action` 的 Raw Event。每项的 subject 固定为 `用户 <user_ref>`；缺少稳定 `user_ref` 时 EpisodeBuilder 直接失败。Required Action ID 必须属于当前 Episode，不能重复或乱序。

数组顺序是 L2 输入顺序。Raw Event 保存 `session_position`，L1 保存 Episode membership；两者共同恢复顺序。Episode 持久层不保存 Context、Action 或 Required Action；L2 只把实际支撑 ExtractionItem 的 Event Context 复制到 Evidence。

一个 Episode 可以包含多次发生。Episode 数量不能表示发生次数。L2 每次只读取一个 Episode。

## L2 目标

L2 从当前 Episode 中找出值得长期保存、能够回到直接依据的主张。

一个 Episode 可以输出零条或多条 ExtractionItem。存在 Required Action 时，输出必须至少包含覆盖它们的 ExtractionItem；没有 Required Action 时仍可输出零条。每条 ExtractionItem 表示一次独立支撑，并列出共同支持完整主张的最小 Raw Event 集合。

L2 只抽取当前 Episode 直接支持的内容。跨 Episode 关联和行为规律归纳交给巩固。

## L2 抽取接口

```text
MemoryItemExtractor.extract(EpisodeInput) -> L2Result

Relation {
  predicate: text
  target: text | null
}

ExtractionItem {
  subject: text
  relations: Relation[]
  statement: text
  condition: text | null
  context: text
  entity_mentions: text[]
  raw_event_ids: text[]
}
```

`relations` 至少包含一项。关系名使用输入语言的开放文本，不设固定枚举。每个关系有自己的可选目标。`statement` 是完整主张和权威语义。

`relations` 必须保留每个主动操作中用于区分该操作的语义，不能将具体动作泛化为更宽泛的动作类型。只在 statement 保留这些语义不算关系完整；正文中的一般背景不要求逐项转成 relation。

2026-09-14 采用该 Prompt 要求。同期对照中，长期主义续播从0/3提升到3/3，决策偏差续播仍为0/3，两个复合动作控制没有退化。用户明确接受这项局部改善和已知剩余失败；原实验的全部目标通过门仍记为未通过，不改写为整体修复。证据见 `experiments/2026-09-14-relation-semantics-diagnosis.md`。

`EpisodeEventInput.action` 已由上游标注动作主体、detail 和 payload 的变化后状态。非系统主体 detail 中属于该角色的主动操作必须进入主张；主动操作使用完成状态表达时，仍按该角色的一次动作处理。系统主体 detail 中的系统事件、车辆状态和设备状态不能改写成用户动作，也不能因为 Episode 中有稳定用户引用就归因给该用户。payload 不能按字段自动拆成独立动作。Context 只描述环境和状态，不能单独证明主体动作。`predicate` 单独阅读时必须能说明关系或动作类型，并包含领域对象。`target` 保存具体对象、动作作用范围或结果值。

同一主体、相关过程、condition、同一组 Raw Event 依据和同一生命周期内的复合动作可以合并为一个 ExtractionItem。`relations` 的数组顺序表达来源明确给出的动作顺序。

MemoryItemExtractor 的第一次 LLM 调用读取完整 Episode，生成 `subject`、`relations`、`statement`、可选 `condition`、候选 `entity_mentions` 和 `raw_event_ids`。存在非空 relation target 时，第二次调用只读取有序 target 文本，并返回值得建立 Entity 身份的索引。

Required Action 是来源确认的必须输出，不接受长期价值准入过滤。每个 Required Action 必须被同一 subject 的至少一条 ExtractionItem 覆盖，对应 `raw_event_id` 必须进入该 item。MemoryItemExtractor 在第二次调用前校验覆盖；漏项和 required Action 下的空结果直接失败，不重试，也不生成 fallback。LLM 仍负责 relations、statement、condition 和 entity_mentions；Required Action 本身不能支持输入中没有出现的语义。

`entity_mentions` 是临时字段。第一次调用列出 statement 和 condition 中除完整 subject 外的具名 Entity 候选。组件删除非原文、完整 subject 和重复候选，并按原文位置排序。组件移除候选中的完整 target，再只把第二次调用选中且逐字出现在主张原文中的完整 target 按原文位置合并进最终字段。选中但不在原文中的 target 不建立 Entity mention，relation target 文本仍保留。MemoryStore 通过精确文本匹配把完整 target mention 写入 `target_entity_id`，其他 mention 写入 `entity_refs`。两次调用都不输出 Entity ID。

LLM 可以省略值为空的 `target`、`condition` 和 `entity_mentions`。最终 ExtractionItem 中的空值分别为 `null`、`null` 和 `[]`。LLM 不输出 `context`、数据库 ID、实体 ID、Evidence ID、状态或置信度。MemoryItemExtractor 根据 `raw_event_ids` 找到对应 Episode Event，按 Episode 顺序把每段格式化为 `[raw_event_id] EpisodeEventInput.context`，以换行符拼接，并把结果附加到 ExtractionItem。

LLM 输出的 `raw_event_ids` 必须按 Episode 顺序排列，不能重复。MemoryItemExtractor 严格校验这些 ID 都属于当前 Episode。

## L2 持久结构

```text
Entity {
  id
  name
  aliases[]
  types[]
  canonical_entity_id
}

Profile {
  entity_id
  text
}

MemoryItem {
  id
  subject_entity_id
  relations: [{predicate, target?, target_entity_id?}]
  statement
  condition
  entity_refs
  status: active | candidate | inactive
}

Evidence {
  id
  memory_item_id
  raw_event_ids[]
  context
}
```

事实、行为、偏好和条件规律共用 MemoryItem。

Entity 的完整规则见 [实体设计](07-entity.md)。`Entity.id` 无语义、唯一且稳定。`name` 不唯一，`aliases` 保存稳定使用的其他名称。名称和别名可以匹配一个或多个 Entity，不能单独证明身份相同。

`canonical_entity_id=null` 表示身份尚未确认；等于自身 ID 表示已确认独立；指向另一个 Entity 表示已确认是同一身份。合并不改写已有 MemoryItem 中的 Entity 引用，Evidence 保持不变，检索时归一后联合读取。

Profile 保存调用方显式维护的当前自然语言描述。调用方提供稳定 ID，服务不要求该 ID 对应已存在的内部 Entity。每个 ID 最多一个 Profile。服务不做 canonicalize。`text` 接受任意字符串；空字符串表示 Profile 已存在但内容已清空。当前系统不从 Session、MemoryItem 或 LLM 生成和巩固 Profile。Profile 不参与检索和召回。

L2 不输出 Entity ID。写入层使用当前 Episode、来源明确提供的信息、名称、别名和已有关系解析实体。来源确认一个新的独立身份时，创建 `canonical_entity_id` 等于自身 ID 的 Entity；能够定位当次具体对象但无法确认长期身份时，创建占位 Entity；同名且没有身份依据时不能强行合并。已经明确属于同一 Entity 的稳定名称可以加入 `aliases`。

每个 Memory relation 都有自己的 `predicate` 和可选 `target`。已经绑定 `Entity.id` 的目标保存 `{entity_id}`，普通值保存完整文本。关系顺序与 ExtractionItem 一致。

`MemoryStore` 不执行 relation target 的语义判断。它只把上游已经选中并进入最终 `entity_mentions` 的完整 target 精确绑定到 `target_entity_id`。其他 target 只保存文本。主体和 mention 的当前绑定规则、SQLite 表和事务边界见 [L2 持久化](08-memory-store.md)。

服务写入使用 `MemoryStore.write_complete_session()`。它用一个事务保存完整原始
Session JSON、MemoryItem、Evidence、FTS5 和 embedding。embedding 根据事务内写入
Evidence 后的 MemoryItem 状态生成并校验。Session ID 在单个数据库中唯一。空
L2Result 仍保存原始 Session。
SourceSession 同时记录调用方明确提供的来源格式和 UTC 写入时间。现有 `write()` 和
`index_embeddings()` 只保留给实验脚本分步使用。

MemoryStore 提供 `get_adjacent_memory_items(entity_ids)`，批量返回输入 Entity 作为主体、relation target 或普通 mention 出现的 MemoryItem。同一 MemoryItem 只返回一次。该接口不做状态过滤、相关性排序或多轮扩展。

`statement` 保存完整主张，也是 MemoryItem 的权威语义。转述、来源明确表达的时间、角色、不确定性和因果不能在抽取时丢失。

`condition` 保存自然语言 IF 条件。数值、模糊描述和复合条件不拆成 dimension/value。

`statement` 和 `condition` 各自使用单行非空文本，不能包含 CR 或 LF。多条依据
仍通过 Evidence.context 表达，不能在一条主张内伪造多个 Markdown 项。

`entity_refs` 绑定 statement、condition 或 relation predicate 中除完整 subject 和完整 relation target 之外的具体实体。复合 target 内部具有独立身份的实体也可以绑定。它不表达角色、数量或 AND/OR。

## Evidence

每次独立支撑写一条 Evidence。相同主张可以复用 MemoryItem，但不能把多次发生或多次独立支撑折成一条 Evidence。

直接抽取时，Evidence 保存 MemoryItemExtractor 确定性生成的 `context`。巩固不复制或新建 Evidence。每次独立来源通过 ConsolidationSupport 连接到 candidate，并保存实际使用的原文片段。Evidence 不保存 Episode、Session、独立时间字段或次数；这些信息沿 SourceSession 和 Raw Event 回读或实时计算。

Episode 数量不能当作支撑次数。支撑数量可以统计 Evidence；行为发生次数还要结合每条 Evidence 指向的原文判断。

## 条件与偏好

用户明确说“下雨开车时我想听萧敬腾”时，L2 可以直接写带 condition 的 active MemoryItem。

系统只观察到一次雨天驾驶点播时，L2 写行为主张：

```text
statement: "用户A在下雨且驾驶过程中请求播放萧敬腾"
condition: null
```

多次直接行为可以由巩固归纳成：

```text
statement: "用户A更倾向于听萧敬腾的歌"
condition: "下雨且正在驾驶时"
status: candidate
```

巩固产生的每条规律都必须通过 ConsolidationSupport 保留直接来源 Evidence。推断结果不能作为更高层推断的原始证据。

## 多人物与 Group

一个 MemoryItem 只有一个主要主体。

“孩子在车上时，用户更可能去学校”以用户为主体。具体孩子通过 condition 和 entity_refs 表达。

“用户A的任意一个孩子”是通用角色条件，不绑定当前已知孩子的 ID，但保留孩子与用户A的关系范围。

一次临时同车组合不创建 Group。只有来源明确把一个稳定集体作为共同主体时才使用 Group。群体主张不能自动推导为每个成员的个人主张。

## 状态

- `active`：系统正在采用的主张。
- `candidate`：巩固产生、尚待验证的假设。
- `inactive`：系统停止采用的主张。

状态不表示现实中的事情此刻是否正在发生。历史时间含义保留在 statement 和 Raw Event 中。

## 巩固边界

重复行为巩固的 Candidate Discovery 使用结构化 few-shot 运行基线：以图书购买、
平板解锁与文档续编、投影亮度增量三个示例说明完整动作归纳和编号引用。
2026-09-22 按用户决定接入已测版本；输入投影、候选契约、Condition 和校验保持原样。
该版本的 realism 结构合法输出为21/21，最终合格规则仍为0/14。当前将其作为继续
优化的运行基线，不视作端到端效果验收通过。

Condition 输入显式提供候选完整有序动作。允许固定动作支持组的严格多数 Evidence
支持条件：8条至少5条。保留重复观察，以 Evidence 标签计数。每项事实显式输出
Evidence 标签和短句编号；多个事实合成 AND 时，完整条件的共同支持交集也须超过半数。
最终只保存交集来源的 Support，未选来源不充当完整条件依据。标签和编号校验保留，
不新增语义蕴含校验。旧位置式引用结构不再接受。

Condition 输入允许多条动作前系统观察，按原始顺序展开并连续编号。取消“恰好一条”
限制；输入生成与引用恢复使用同一编号，动作后观察不进入输入。不在此步骤增加
语义蕴含校验，也不做状态覆盖。无系统观察时的 Context 路径保持原样。

巩固可以重读 Entity、MemoryItem、Evidence、Episode 和 Raw Event。它负责跨 Episode 关联、条件归纳、候选修订、确认占位 Entity 的身份、判断新 MemoryItem 对已合并 Entity 的引用归属，以及在最终 Entity 再次合并时维持 Canonical Entity 指向。

重复行为巩固遇到“主驾座椅通风”跨 Episode 引用冲突时，依据同车、明确动作及
目标字段确认功能对象身份，原子保存身份确认与 candidate。只处理尚未确认的占位
实体，其他冲突保持失败；完整条件见 [实体设计](07-entity.md)。固定 Qwen 输出重放
已保存 1 条 candidate 和 6 条 Support；这不代表完整 realism 提取成功率。

candidate 如何转为 active、反馈如何关联候选、谓词如何大规模归一，尚未设计完成。

## 检索边界

检索请求只包含自然语言 query、context 和实验参数 `limit`、`seed_limit`、`neighbor_limit`、`rrf_k`、`bm25_weight`、`entity_match_weight`、`evidence_support_weight`。query 和 context 至少一个非空。调用方不提交 Entity ID、查询类型、跳数或查询计划。

QueryResolver 是 Resolver 的确定性子类。它每次读取当前 Entity，以 `name`、`aliases` 和 `scope_text` 匹配 query/context。重叠名称保留最长表面词，同名保留全部 ID，无法确定的代词不生成 ID。它不调用 LLM，不改写输入。

MemoryStore 使用 jieba + FTS5 索引 `statement + condition + 全部 Evidence.context`。每条 MemoryItem 只保存一条 embedding。恰好一条 Evidence 时，embedding 文本为 `statement + condition + Evidence.context`；没有 Evidence 或多于一条 Evidence 时，只使用 `statement + condition`。新增 Evidence 后同步重建该 MemoryItem 的唯一 FTS5 文档和 embedding。

BM25、embedding cosine、`entity_match_score`、`evidence_support_score` 和最终 RRF 分别保留。排名使用请求中的 `rrf_k`、`bm25_weight`、`entity_match_weight` 和 `evidence_support_weight`，embedding 权重固定为 `1`。`entity_match_score` 是查询与 MemoryItem 共同引用的不同非主体 Canonical Entity 数量；每个命中增加 `entity_match_weight / (rrf_k + 1)`。重复 mention 和同一 Canonical Entity 只计一次，只匹配 subject 不加分。MemoryItem 有至少两条 Direct Evidence 时，`evidence_support_score=1`；零条或一条时为 `0`。两条和更多条 Evidence 的加分相同。相同原始分数使用 competition rank 共享名次，MemoryItem ID 只打破展示顺序平分。

task-001 的 statement-only BM25 固定重排中，权重 `2` 把 Hit@1 从 `3/5`
提高到 `4/5`。BM25 文档加入 Evidence context 后，五条正确记忆的 BM25 都排
第一；权重 `3` 时最终 Hit@1 为 `5/5`、Hit@5 为 `5/5`。当前采用
`bm25_weight=3.0`。记录见
`experiments/2026-07-29-retrieval-combined-bm25.md`。

固定上一轮 13 个 MemoryStore 只重放检索时，`entity_match_weight=0.1` 将两条
可区分排序 case 从第 2 提升到第 1，其余 63 条排名不变。Hit@1 从 `59/65`
提高到 `61/65`，Hit@5 保持 `62/65`。当前采用该权重。记录见
`experiments/2026-07-29-retrieval-exact-nonsubject-entity-score.md`。

固定单 Evidence Context 版本的 13 个 MemoryStore 只重放检索时，把二值非主体 Entity
命中改为不同 Canonical Entity 命中数，使 `session-0035` 从 rank 2 到 rank 1，其余
64 个 Positive 和 52 个 negative 排名不变。Hit@1 从 `62/65` 提高到 `63/65`。
当前采用 Entity 命中计数。记录见
`experiments/2026-08-18-entity-match-count-ablation.md`。

固定 Entity 命中计数版本的 13 个 MemoryStore 只重放检索时，给至少两条 Direct
Evidence 的 MemoryItem 增加一次 `0.25 / (rrf_k + 1)`，使 `session-0194` 和
`session-0195` 到 rank 1，`session-0192` 从 rank 1 回退到 rank 2。Positive Hit@1
从 `63/65` 提高到 `64/65`，52 个 negative 排名不变。实验未满足预注册的零回退门禁。
产品决策接受该已知回退，以总体 Top-1 净改善为采纳标准。当前采用该固定封顶加分。
记录见 `experiments/2026-08-18-bounded-evidence-support-ablation.md`。

检索评测允许一条 positive 对应多个可接受的 history Session。Oracle 同时标注
每个 history 中必须进入 Evidence 的动作 Raw Event。MemoryItem 只有在来自可接受
history，且至少一条 Evidence 包含对应动作 Event 时才算正确。Evidence 可以额外
包含背景 Event；最小 Evidence 质量由 L2 评测单独检查。评测要求检索到有用记忆，
不要求复现唯一 Episode。具体标注见
`experiments/2026-07-29-retrieval-evidence-oracle.md`。

检索先取 Text Seed，再用 QueryResolver 找到的每个 Entity 分别补充 Entity Seed。两类 Seed 去重后做两轮邻接：

```text
召回 Seed MemoryItem
  -> 第一轮邻接扩展
  -> 第二轮邻接扩展
  -> 返回 Retrieval Result 诊断路径
```

`neighbor_limit` 按每轮、每个 Entity 单独应用。路径内不能重复 MemoryItem；第二轮不沿进入当前项的 Entity 原路返回。只共享 subject 的 MemoryItem 不组成路径。relation target 和 entity_refs 可以连接，后者只表示共同提及。

没有扩展时 Seed 形成单 Step 结果。扩展成功时，新路径替代前缀。不同分支分别形成结果。路径按各 Step 的 `rrf_rank` 序列排序，再应用 `limit`；`limit` 不截断已选路径。

应用 `limit` 后，Retriever 沿已选路径中 MemoryItem 的 ConsolidationSupport 正向查找
candidate，并在最早来源 Step 后追加 `stage=support` 的终止 Step。同一 candidate 的多个
来源合并到一个 Step。Support 不参与排名或 `limit`，也不继续 Entity 邻接。candidate
直接命中时不反向展开来源 MemoryItem。

当前 RetrievalResult 只包含带 MemoryItem、stage、seed/via 和全部分数的 Step。active、candidate、inactive 原样返回。检索不判断结果是否足以回答问题。

RetrievalResultFormatter 保留 Retriever 的诊断结果，并把同一结果确定性投影为
Agent Markdown。每个 RetrievalResult 形成一个 `### 相关记忆[排名N]` 分组，
`N` 是返回列表中的一基顺序。组内按 Step 顺序输出 statement，并把非空
condition 追加为 `（适用条件：...）`。candidate 和 inactive 分别添加
`[候选]`、`[已失效]`；active 不添加标签。不同分组保持自包含，允许重复同一
MemoryItem。

Agent Markdown不输出请求query/context、MemoryItem/Entity ID、relations、entity_refs、stage/via、Step排名或分数。空结果固定输出`未检索到相关记忆。`。

### Evidence选择与返回

2026-09-08用户明确决定采纳BM25 top-3并进行Realism验证。上一轮DeepSeek下游实验有回退，采纳来自用户决定，不把原实验门改写为通过。

Formatter从MemoryStore读取每条已选MemoryItem的Direct Evidence。在该集合内对完整Evidence.context用jieba与SQLite FTS5 BM25分别计算query分数和请求context分数，按query分数降序、context分数降序、Evidence ID升序选最多3条。无匹配也按相同顺序选，不加入阈值、时间过滤、LLM或额外embedding调用。

每份Evidence紧随所属主张输出“历史发生背景”，附Evidence ID、来源Session ID和Raw Event ID列表，完整保留context及其内部顺序。候选不足3条全部返回，无Direct Evidence不补背景。ConsolidationSupport继续通过原Support Step表达，不将其他MemoryItem的来源冒充candidate的Direct Evidence。

不同Evidence保持独立发生；背景不自动成为当前状态、因果解释或Condition。固定必要背景与内部片段选择暂不处理，总返回预算暂缓。主张召回与排序、statement、condition、状态及路径limit不变。Raw Event额外回读、时间选择和predicate过滤尚未实现。

前轮选择实验（研发仓库历史记录：`../../docs/progress/limem-evidence-bm25-top3-20260908.md`；本发行仓库不附带）与下游回退记录（研发仓库历史记录：`../../docs/progress/limem-evidence-top3-decision-deepseek-20260908.md`；本发行仓库不附带）保留有效边界；本次实现及Realism实验记录见协议（研发仓库历史记录：`../experiments/2026-09-08-realism-evidence-top3.md`；本发行仓库不附带）。

## 最小通路

```text
完整 Session
  -> RawDataParser 输出 SessionInput / Raw Event
  -> EpisodeBuilder 输出 Episode
  -> L2 输出 ExtractionItem
  -> 写入 MemoryItem 和 Evidence
  -> 检索返回 Retrieval Result
  -> RetrievalResultFormatter 返回 Agent Markdown
```

最小通路已闭合到 Agent Markdown。下一步分别验证真实领域 query 的排序质量、
Evidence选择效果和时间选择，不把这些问题混入当前两轮Entity路径。

## 服务边界

服务只使用 `X-API-Key` 鉴权。OpenAPI 使用 `XApiKey` apiKey security scheme 表达该请求头。
用户 API key 本身就是身份。每个记忆库只归属于签发它的 key；新 key 不继承旧 key 的数据。管理员可以签发、列出和撤销用户 key，
也可以检索或删除已知记忆库。管理员不能创建、列出或写入用户记忆库。

公开业务路径固定为：

```text
POST   /v1/admin/api-keys
GET    /v1/admin/api-keys
DELETE /v1/admin/api-keys/{key_id}
POST   /v1/limem
GET    /v1/limem
DELETE /v1/limem/{memory_bank_id}
POST   /v1/limem/session-formats/vehicle-session-v1/validate
POST   /v1/limem/{memory_bank_id}/sessions
POST   /v1/limem/{memory_bank_id}/retrieve
POST   /v1/limem/{memory_bank_id}/consolidations
POST   /v1/limem/{memory_bank_id}/auto-consolidations
GET    /v1/limem/{memory_bank_id}/auto-consolidations/{run_id}
POST   /v1/limem/{memory_bank_id}/auto-consolidations/{run_id}/cancel
GET    /health
```

服务还提供只读 Markdown 文档：`/guide`、`/guide/authentication`、
`/guide/memory-banks`、`/guide/session-formats/vehicle-session-v1` 和
`/guide/errors`。不开放 FastAPI 默认的 `/docs`、`/redoc` 和
`/openapi.json`。

创建记忆库时必须固定 `source_format`。`type` 可以省略，默认 `temporary`。
temporary 创建 24 小时后不可访问，48 小时后删除文件和元数据；persistent 不自动
过期。用户和管理员显式删除都立即物理删除。撤销 key 时先使 key 失效，再同步删除
其全部 temporary 和 persistent 记忆库；签发记录保留为 revoked。

Session 格式校验接口要求有效的用户 key，只运行 `vehicle_session_v1` 对应的
RawDataParser。它返回 Session ID 和 Record 数量，不调用 LLM、embedding 或
MemoryStore，也不检查某个记忆库内是否已经存在相同 Session ID。

Session 写入同步完成，且同一 Session 的原始 JSON、MemoryItem、Evidence、FTS 和
embedding 使用一个事务提交。相同 `session_id` 在单库内拒绝重复。空 L2 仍保存
原始 Session。失败不留下该 Session 的部分数据。

同一记忆库的写入、检索和删除使用同一把锁。不同记忆库可以并行。服务只运行一个
Uvicorn worker。检索请求只暴露必填可空的自然语言 `query`、`context` 和可选
`limit`；两段文本不能同时为 null，`limit` 默认 3，范围 1..10。内部检索参数读取
`config/service.toml`，调用方不能覆盖。

手动巩固同步处理请求指定的 Canonical Subject。自动巩固由接口显式启动，不决定启动
时机。请求固定一个 Bank、一个 Consolidator 和 `max_entities`。服务临时统计每个
Canonical Subject 的待巩固 Evidence，只选择至少 5 条的 Subject，按数量降序、Entity
ID 升序处理。同一 Bank 同时只允许一个手动或自动巩固。运行状态只保存在当前进程。
取消在模型或 embedding 返回后、以及写事务开始前检查；已经开始的单个写事务不打断。

服务不提供旧接口兼容、数据迁移、重试、fallback 或恢复路径。部署直接替换主机
`127.0.0.1:8012` 上的旧容器服务。旧容器只停止；旧数据、仓库、镜像和配置保留。

## 车辆抽取输入：状态池差分

采纳2026-09-15消融中的差分输入。车辆来源适配代码按Session维护状态池，首观测初始化，后续发送全部观测差分和固定目录中的原话/对象信息。显式null、空串和缺失保持各自含义；before注明最近观测的时间与来源。暂不筛除Event。

服务写入和Qwen评测调用build_vehicle_claim_episode生成抽取视图。MemoryItemExtractor.extract接收完整episode与claim_episode；LLM只读claim_episode，引用校验和Evidence.context使用完整episode。两个视图只允许context不同。不增加LLM调用，不修改抽取prompt、输出schema或解码参数。

两组各220条：机械通过146→203，输入token总量降低62.66%，格式错误12→6，复读9→9，截断10→6。按用户确认的总体改善标准采纳，不要求零退化。64条恢复、7条新增失败全部保留；203条机械通过不表示全部语义正确。完整抽取、TargetEntity和检索的效果待验证。依据见状态池消融（研发仓库历史记录：`../experiments/2026-09-15-qwen-state-pool-ablation.md`；本发行仓库不附带）。

## 状态池展示：采纳Parser自然语言映射

2026-09-17按用户明确决定采纳。固定实验中输入token下降9.35%，首调用通过204→200，11条恢复、15条新增失败。预设数值门槛未通过，用户仍选择此版本作为后续基线；不声称本轮整体通过率改善。直接复用Parser既有自然语言映射，字段所属对象、前值来源、事件与动作要求保持不变。实现完整输入与220条实验候选逐字一致。映射版的TargetEntity、写入、检索尚未重测；201/220属于映射前结果。依据见自然语言映射消融（研发仓库历史记录：`../experiments/2026-09-16-qwen-state-language-ablation.md`；本发行仓库不附带）。

## 行为偏好Agent：固定离线基线

2026-09-23按用户决定，将`behavior-react-20260923`固定到`src/limem/consolidator/behavior_agent/`。该模块只归纳重复行为Y，condition为null，条件归纳与MemoryStore保存尚未接入。服务仍使用现有巩固流程，本次不上线。

Agent使用Qwen3-1.7B，每例最多6轮，单次输入6000、输出512 token。工具为propose、inspect、review、finish。核对阶段仅依据已读操作重新归纳；引用至少两次操作、包含新来源且无缺失信息时进入结束状态。参数由动态Schema约束，旧候选保留在轨迹中。

13项离线验证提交5项，3项行为完整正确，2项部分正确；参数错误0次。核对仍可能丢失目标档位，查询仍可能反复为空。依据见当前基线实验（研发仓库历史记录：`../experiments/2026-09-23-behavior-agent-refinement.md`；本发行仓库不附带）与核心迁移约定（研发仓库历史记录：`../experiments/2026-09-23-behavior-agent-core-baseline.md`；本发行仓库不附带）。


## LLM 服务接入配置

2026-09-28 支持通过同一 OpenAI-compatible Chat Completions 接口接入 DeepSeek、Ollama、SGLang 和 vLLM。用户自行部署模型，LiMem 从 `config/llm.toml` 读取 API 根地址、模型名和参数，从 `.env` 中指定变量读取凭据。

供应商扩展字段为可选的强类型配置：DeepSeek `thinking.type=disabled`，或支持该模板参数的本地模型 `chat_template_kwargs.enable_thinking`。省略时不发送扩展字段。未知字段和混合配置直接失败。

此次改动只调整模型接入配置，不改变 Prompt、JSON 输出模式、解析器、抽取和检索算法。SDK 自动重试仍关闭。配置与传输合同验证不代表各模型的语义效果已经通过。
