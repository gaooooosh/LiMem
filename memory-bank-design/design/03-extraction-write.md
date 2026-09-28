# L2 抽取与写入

## 目标

L2 每次读取一个 Episode，找出其中值得长期保存的直接主张，并为每条主张指出原始依据。

Episode 只限定一次模型输入。它不决定抽取项数量，也不决定哪些 Raw Event 共同支持一条主张。

## 准入

一条内容进入 L2 前需要同时满足：

1. 它的记忆价值能够超出当前 Episode。
2. 它关于一个可以定位的个人、集体、地点、设备或其他实体。
3. 它是当前用户世界中的特有信息，不是通用知识。

不满足准入条件的内容仍保留在 L0/L1。瞬时界面状态、普通系统应答和通用百科问答不生成 MemoryItem。

一次请求、采纳、拒绝或执行结果可以作为以后归纳偏好的直接行为依据。它们不能在 L2 阶段自动变成偏好。

## 输入

```text
EpisodeInput {
  episode_id: text
  events: [
    {
      raw_event_id: text
      context: text
      action?: text
    }
  ]
}
```

`context` 和 `action` 已由 EpisodeBuilder 组织。来源明确提供的用户、动作主体、时间和传感器含义保留在文本中。来源没有提供的值保持未知。没有 Action 的 Raw Event 省略 `action`。

`action` 已标注主体、detail 和 payload 的变化后状态。非系统主体 detail 中明确写出的有长期价值动作属于该主体。系统主体 detail 中的系统事件、车辆状态和设备状态不能改写成用户动作，也不能因为 Episode 中有稳定用户引用就归因给该用户。payload 只表达事件发生后的状态，不能把其中每个字段自动拆成独立动作。`context` 只描述动作发生时的环境和状态，不能单独证明某个主体执行了动作。

L2 只读取 EpisodeInput，不另外读取 SessionInput。当前一个 Episode 包含同一 Session 的全部 Raw Event。

车辆链路在来源适配范围生成claim_episode。程序维护Session内状态池，以观测差分和原话/对象信息替换LLM输入context，保留所有Event、Action、Required Action及其顺序。首快照仅初始化状态池；null和空串是显式变化，缺失不当作当前观测。before保留来源和时间。完整Episode仍供引用校验、Evidence.context拼接及写入使用。普通背景无需由LLM生成；action原文中已有的背景暂不删除。

接口为extract(episode, claim_episode=claim_episode)。claim_episode只允许context与完整episode不同；未提供单独视图的非车辆调用直接读取原episode。

## 输出

```text
MemoryItemExtractor.extract(EpisodeInput) -> L2Result

L2Result {
  items: ExtractionItem[]
}

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

ExtractionItem 是临时结构。第一次 LLM 调用读取完整 Episode，输出 `subject`、`relations`、`statement`、可选 `condition`、候选 `entity_mentions` 和 `raw_event_ids`。存在非空 target 时，第二次调用只读取 target 数组并返回 Entity target 的索引。LLM 不生成 `context`、数据库 ID、实体 ID、Evidence ID、status 或 confidence。组件严格校验、合并 target mention 并生成 `context`，再返回完整 L2Result。

第一次调用没有输出 items 时直接返回空结果。非空结果没有 target 时不执行第二次调用。

### `subject`

主张主要关于谁。L2 可以结合当前 Episode 把代词写成能够定位的名称或 Session 角色，但不判断跨 Session 身份。来源只能确定角色时，保留可回溯的角色描述，例如“本次主驾用户”。写入层随后绑定已有 Entity 或创建占位 Entity。

### `relations`

至少包含一项。每项关系包含输入语言的简短开放文本 `predicate`，并拥有自己的可选 `target`。当前不设置谓词枚举。`predicate` 单独阅读时必须能说明关系或动作类型，并包含动作涉及的领域对象，不能只写“设置”“切换”“开启”或“关闭”。`predicate` 不能代替完整 `statement`。

同一主体、相关过程、相同 condition、同一组 Raw Event 依据和同一生命周期内的复合动作可以放入一个 ExtractionItem。`relations` 按来源明确给出的动作顺序排列。条件、依据或主体不同的动作不能为了减少条目而合并。

`relations` 必须保留区分具体主动操作的语义。例如续播不能泛化为普通播放，即使 statement 已经保留续播含义。一般背景不要求逐项转成 relation。

每项 `target` 保存该关系的具体对象、动作作用范围或结果值。第二次调用判断完整 target 是否值得建立 Entity 身份。它只选择能够区分记忆内容、可被其他记录引用、且整体指向一个对象的 target。普通值、动作范围、通用界面页面、路线和对象间关系不选。没有自然目标时省略该项的 `target`。

### `statement`

当前 Raw Event 直接支持的完整主张，也是 ExtractionItem 的权威语义。它需要保留来源明确表达的转述、时间、角色、不确定性和因果。

`statement` 必须是单行非空文本，不能包含 CR 或 LF。

来源明确表达因果时可以保留。两个状态只是在 Episode 中邻近或先后出现时，只能表达来源支持的事实和顺序，不能补写因果。

### `condition`

来源明确表达的 IF 条件。最终 ExtractionItem 无条件时使用 `null`；LLM 响应可以省略该字段。

非空 `condition` 必须是单行文本，不能包含 CR 或 LF。

单次行为发生时的环境不能直接写入 `condition`：

```text
statement: "用户A在下雨且驾驶过程中请求播放萧敬腾"
condition: null
```

用户明确表达条件偏好时可以直接写入：

```text
statement: "用户A喜欢听萧敬腾的歌"
condition: "下雨且正在驾驶时"
```

### `entity_mentions`

第一次调用的 `entity_mentions` 列出 `statement` 和 `condition` 中除完整 subject 外、需要身份消解的具体实体候选。组件移除其中所有完整 target，再只把第二次调用选中的完整 target 合并进最终 `entity_mentions`。该字段不绑定 Entity ID。

```text
condition: "小明和小红都在车上时"
entity_mentions: ["小明", "小红"]
```

“用户A的任意一个孩子”“小明的朋友们”这类通用角色不作为具体实体列出。输出按每个 item 的原文出现顺序排列。每个 mention 必须逐字来自 statement 或 condition。该字段只服务本次写入，不持久化。

没有实体时 `entity_mentions=[]`。第一次调用给出候选，组件删除非原文、完整 subject 和重复项，并按 statement/condition 原文位置排序。第二次调用选中的 target 只有逐字出现在主张原文中才进入最终 mentions；否则只保留 relation target 文本。第二次调用的索引重复、乱序或越界直接失败。不重试或模糊修补。

### `raw_event_ids`

列出共同支持完整 `statement + condition` 的最小 Raw Event 集合。ID 必须来自当前 Episode，按 Episode 顺序排列，不能重复。

- 两条 Raw Event 共同表达主体和行为时，放入同一个抽取项。
- 同一行为独立发生两次时，输出两个抽取项。
- 同一个 Raw Event 可以支持多个不同主张。
- Episode 中只提供背景、没有直接支持主张的记录不能加入依据。

### `context`

MemoryItemExtractor 根据 `raw_event_ids` 找到当前 Episode 中的 Event，按 Episode 顺序生成 `context`。每段使用以下格式，并以换行符连接：

```text
[raw_event_id] EpisodeEventInput.context
```

LLM 不复制、改写或概括 context。`context` 只包含支撑当前 ExtractionItem 的 Event Context，不加入其他邻近 Event，也不包含 Action。

## 抽取规则

### 直接事实与行为

L2 可以抽取来源直接支持的事实、明确陈述和行为：

```text
用户A请求播放萧敬腾
副驾用户发起前往学校的导航
小王说小李去年住在北京
```

L2 不能补写没有依据的结果。例如“发起导航”不能写成“到达目的地”，“播放歌曲”不能写成“用户听完并喜欢这首歌”。

### 复合动作

同一主体在一个相关过程中完成连续动作时，可以输出一个 ExtractionItem。以下来源明确写出“先关闭勿扰模式，随后恢复播放《稻香》”：

```yaml
subject: "本次主驾用户"
relations:
  - predicate: "关闭勿扰模式"
  - predicate: "恢复播放歌曲"
    target: "《稻香》"
statement: "会议结束后，本次主驾用户关闭了勿扰模式，随后恢复播放《稻香》"
condition: null
entity_mentions: []
context: "[record-before] <会议结束时的 Context>\n[record-action] <动作发生时的 Context>"
raw_event_ids: [record-before, record-action]
```

这里的数组顺序来自来源明确表达的动作顺序。“会议结束后”只表达来源支持的时间关系；不能写成会议结束导致恢复播放。

### 明确偏好

用户直接表达“我喜欢爵士”或“下雨开车时我想听萧敬腾”时，L2 可以抽取偏好。写入后的状态为 `active`。

### 行为推断偏好

一次或多次点播行为本身仍是行为主张。跨 Episode 汇总行为并归纳偏好属于巩固。

### 多人物

一个抽取项只有一个主要主体。其他人物根据语义放在 relation target、statement 或 condition 中，并通过 `entity_mentions` 交给写入层消解。

“孩子在车上时，用户更可能去学校”以用户为主体，孩子是条件参与者。临时同行者不组成 Group。

## 写入

首版写入实现见 [L2 持久化](08-memory-store.md)。当前只把 `用户 {ref}` 绑定到 Raw Event 明确提供的同一 `user_ref`；其他主体和 `entity_mentions` 创建 Episode 范围占位 Entity。与完整 target 相同的 mention 绑定到 relation 的 `target_entity_id`，不重复进入普通 `entity_refs`。首版不执行下面描述的通用实体解析。

写入层按以下顺序处理每个 ExtractionItem：

1. 使用来源明确提供的信息、当前 Episode、Entity 的名称和别名以及已有关系，为 `subject`、全部 relation target 和 `entity_mentions` 查找可能的 Entity。
2. 只有依据能够确定唯一身份时才绑定已有 Entity。来源确认一个新的独立身份时，创建 `canonical_entity_id` 等于自身 ID 的 Entity；能够定位当次具体对象但无法确认长期身份时，创建 `canonical_entity_id=null` 的占位 Entity；同名且没有身份依据时不能强行选择。已经明确属于同一 Entity 的稳定名称可以加入 `aliases`。
3. 把 `subject` 写成 `subject_entity_id`，逐项判断 relation target 是实体引用还是普通文本，并把其他具体实体写成 `entity_refs`。命中已合并 Entity 时，使用巩固对引用归属的判断。完整规则见 [实体设计](07-entity.md)。
4. 按原顺序写入全部 relations，并在语义相同时复用已有谓词写法。
5. 查找语义相同的 MemoryItem。只有能够确认是同一主张时才复用；否则新建。
6. 为本次独立支撑写一条 Evidence，把 ExtractionItem 的 `raw_event_ids` 和 `context` 原样写入。

直接抽取的新 MemoryItem 使用 `status=active`。

同一 Episode 中两次相同行为可以复用同一个 MemoryItem，但必须产生两条 Evidence。两次行为位于同一 Raw Event 时，两条 Evidence 可以引用相同的 `raw_event_ids`；Raw Event 引用不能充当 Evidence 唯一键。

重复处理同一个 ExtractionItem 时，写入层必须复用原 Evidence ID。具体稳定键属于实现设计。

新主张与旧主张冲突时不能静默覆盖。只有完成替换判断后，旧项才转为 `inactive`。当前 L2 结构不使用有效期字段推断替换关系。

## 与巩固的边界

巩固可以重新读取 Entity、MemoryItem、Evidence、Episode 和 Raw Event。它负责：

- 连接跨 Episode 的相关行为。
- 从多次直接行为归纳偏好或条件规律。
- 确认占位 Entity 的身份。
- 收窄、修订或停止采用已有候选。

巩固产生的假设使用相同 MemoryItem 结构，初始状态为 `candidate`。它通过 ConsolidationSupport 连接已有独立 Evidence，并只保存实际使用的来源片段，不复制 Evidence。

## 验收

1. Episode 中没有长期记忆价值时，`items` 为空。
2. 每个抽取项的 Raw Event 依据能够独立核对。
3. 同一 Episode 中两次相同行为产生两条抽取项。
4. 单次行为的环境不会直接成为 `condition`。
5. 用户明确表达的 IF-THEN 可以直接形成带 `condition` 的 active MemoryItem。
6. 转述、显式时间和不确定性不会在 `statement` 中丢失。
7. 具体条件参与者能够经 `entity_mentions` 写成 `entity_refs`。
8. 重复行为可以复用 MemoryItem，但每次行为保留独立 Evidence。
9. 每个 ExtractionItem 至少包含一项 relation；复合动作的关系顺序与来源一致。
10. 两次 LLM 调用都不输出 context；相同 `raw_event_ids` 在相同 Episode 中得到相同 context。
11. 每个 Episode 只调用一次 LLM；实体字段与主张字段来自同一个严格响应。
12. LLM 响应或确定性校验失败时整个抽取失败，不重试、不修补、不运行备用 Prompt。

车辆状态池内部仍按原始字段和值比较；LLM输入使用Parser的FIELD_RULES、render_leaf与render_payload_value生成自然语言。保留前后值的来源ID和时间。同名字段标签附原路径，未覆盖的字符串值保留原值；不新增映射词典、不删事件、不改Action及Required Action，完整Episode继续用于Evidence。
