# L2 语义层 Schema

## 目标

L2 保存值得长期使用的主张，并保留主张到原始依据的路径。

事实、行为、偏好和条件规律使用同一种 `MemoryItem`。它们的区别由 `relations`、`statement` 和 `condition` 表达。

## 逻辑结构

```text
Entity {
  id: text
  name: text
  aliases: text[]
  types: text[]
  canonical_entity_id: text | null
}

Target =
  text
  | { entity_id: text }

MemoryRelation {
  predicate: text
  target?: Target
}

MemoryItem {
  id: text
  subject_entity_id: text
  relations: MemoryRelation[]
  statement: text
  condition: text | null
  entity_refs: [
    {
      text: text
      entity_id: text
    }
  ]
  status: active | candidate | inactive
}

Evidence {
  id: text
  memory_item_id: text
  raw_event_ids: text[]
  context: text
}
```

数据库索引、embedding、创建时间和批处理元数据属于实现，不属于这份逻辑结构。

## Entity

Entity 为个人、地点、对象、设备和稳定集体提供可复用的身份引用。完整规则见 [实体设计](07-entity.md)。

`id` 无语义、唯一且稳定。`name` 是不要求唯一的显示名称。`aliases` 保存同一实体稳定使用的其他名称。名称和别名可以匹配一个或多个 Entity，不能单独证明身份相同。

`types` 使用开放文本标签，例如 `Person`、`Place`、`Device`、`Group`。它只描述实体类别，不保存驾驶者、乘客或朋友等临时角色。

`canonical_entity_id=null` 表示身份尚未确认；等于自身 ID 表示已确认独立；指向另一个 Entity 表示已确认是同一身份。来源确认一个新的独立身份时，写入层创建指向自身的 Entity；能够定位当次对象但不能确认长期身份时，创建名称可回溯的占位 Entity。

确认同一身份后只更新 `canonical_entity_id`。MemoryItem 保留原 Entity 引用，Evidence 保持不变，检索时归一后联合读取。除表示已确认独立的自指外，合并不能形成多步链或循环。新 MemoryItem 对已合并 Entity 的引用归属，以及最终 Entity 再次合并后的 Canonical Entity 指向，由巩固负责。

一次同车人员组合不能因为共同出现就创建 Group。

## MemoryItem

### `id`

MemoryItem 的稳定引用。ID 由写入层生成，不由 LLM 生成。

### `subject_entity_id`

主张主要关于谁。

写入层把 L2 抽出的主体文本解析成已有实体或占位实体。一个 MemoryItem 只有一个主要主体。句子中出现的其他人不自动成为主体。

主体可以是个人，也可以是真正稳定的 Group。一次临时同车组合不创建 Group。

### `relations`

`relations` 是至少包含一项的有序关系数组。每项关系包含一个 `predicate` 和自己的可选 `target`。数组顺序保留来源明确给出的动作顺序。

`predicate` 使用输入语言的简短开放文本，例如：

```text
请求播放
偏好音乐
倾向前往
是...的父亲
```

`predicate` 用于聚合和检索。它不设置固定枚举，也不代替 `statement`。写入层确认两条关系语义相同时，可以复用已有谓词写法。

关系有明确目标时填写该项的 `target`：

```text
relations:
  - predicate: "请求播放"
    target: {entity_id: "entity-05"}  # 此例指向萧敬腾 Entity
  - predicate: "设置目标温度"
    target: "24°C"
  - predicate: "开启勿扰模式"
```

已经绑定 `Entity.id` 的目标使用实体引用。普通值直接保存完整文本。数值和单位不拆分。没有自然目标时省略该关系的 `target`。

JSON 对象和字符串能够区分实体引用与普通文本，因此不再维护两个互斥字段。

### `statement`

主张的完整自然语言表达。它必须能够独立阅读。

`statement` 使用单行非空文本，不能包含 CR 或 LF。

`statement` 是 MemoryItem 的权威语义。它保留来源明确表达的时间、转述、角色、不确定性和因果：

```text
小王说小李去年住在北京
用户A可能更喜欢安静的餐厅
摄像头记录主驾驶位置有人
```

不能把第一条压缩成“小李住在北京”。Raw Event 已有的精确发生时间不要求复制到正文，可以沿 Evidence 回读。

### `condition`

主张成立或适用的自然语言条件。无条件时使用 `null`。

非空 `condition` 使用单行文本，不能包含 CR 或 LF。

```text
上午
10:00以后
车内炎热时
车内温度超过36°C时
下雨且正在驾驶时
```

`10:00以后` 与 `上午`、`36°C` 与 `炎热` 是不同表达。存储层不声明它们等价。

`condition` 只表示 IF-THEN 关系。一次行为发生时的环境写入 `statement`，不能因为同一时刻正在下雨或驾驶就自动提升为条件规律。

### `entity_refs`

绑定 `statement` 或 `condition` 中除完整 subject 和完整 `relations[].target` 之外的具体实体身份。复合 target 内部具有独立身份的实体也可以绑定：

```text
condition: "小明和小红都在车上时"
entity_refs: [
  {text: "小明", entity_id: "entity-02"},
  {text: "小红", entity_id: "entity-03"}
]
```

`entity_refs` 只固定“小明具体是谁”。角色、数量以及 AND/OR 由自然语言表达。

通用角色不绑定当前已知实体：

```text
condition: "用户A的任意一个孩子在车上时"
entity_refs: []
```

“小明和他的朋友们”只绑定小明。只有原文明确说出具体朋友并完成身份消解时，才增加相应引用。泛化角色时必须保留关系范围，例如“用户A的孩子”，不能只写“任意一个孩子”。

### `status`

- `active`：系统正在采用的主张。
- `candidate`：巩固产生、尚待验证的假设。
- `inactive`：系统停止采用的主张。

`status` 表示系统是否采用一条主张，不表示事情此刻是否正在发生。带有不同时间限定的历史陈述可以同时保持 `active`。

## Evidence

一条 Evidence 表示对一个 MemoryItem 的一次独立支撑。

`raw_event_ids` 保存共同支持这次主张的最小 Raw Event 集合：

```text
Evidence {
  id: "evidence-1"
  memory_item_id: "memory-1"
  source_session_id: "session-1"
  raw_event_ids: ["weather-1", "driving-1", "request-1"]
  context: "[weather-1] 当前下雨。\n[driving-1] 用户正在驾驶。\n[request-1] 车内媒体未播放。"
}
```

直接抽取时，`context` 由 MemoryItemExtractor 根据 `raw_event_ids` 确定性生成。每段以 `[raw_event_id]` 开头，使用对应 Episode Event 的 `context`，并按 Episode 中的顺序逐行拼接。`source_session_id` 指向保存完整来源 JSON 的 SourceSession。巩固不创建新 Evidence；candidate 通过 ConsolidationSupport 保存来源 Evidence ID 和实际使用的原文片段。`context` 保存本次直接支撑发生时的环境，不进入 MemoryItem。

同一行为独立发生两次时，可以复用同一个 MemoryItem，并保存两条 Evidence。Episode 数量不能作为支撑次数；行为发生次数还要结合 Evidence 指向的原文判断。

Evidence 不重复保存以下内容：

- Episode ID：可以沿 Raw Event membership 查询。
- 发生时间：读取 Raw Event 的来源时间。
- 支撑数量：统计 Evidence。
- Action 和完整来源载荷：沿 Raw Event 和所在 Episode 回读。
- 置信度和支持类型：由使用方根据主张及原始依据判断。

`raw_event_ids` 只表示依据，不定义 Evidence 的唯一性。同一条 Raw Event 内含两次独立行为时，可以产生两条 Evidence，并引用相同 Raw Event。

Evidence 的 `id` 标识一次支撑。重复处理同一个 ExtractionItem 时必须复用该 ID；稳定 ID 的生成方式属于实现设计。

## 事实与偏好

事实和偏好不使用不同结构：

```text
relations: [{predicate: "请求播放", target: "萧敬腾"}]
statement: "用户A在驾驶过程中请求播放萧敬腾"
condition: null
```

这是直接行为。

```text
relations: [{predicate: "偏好音乐", target: "萧敬腾"}]
statement: "用户A更倾向于听萧敬腾的歌"
condition: "下雨且正在驾驶时"
```

这是条件偏好。

一次行为只能支持第一条。用户明确陈述偏好时，L2 可以直接写第二条并设为 `active`。根据多次行为归纳出的第二条由巩固写入，初始状态为 `candidate`。

## 多人物

条件参与者不改变 MemoryItem 的主要主体：

```text
subject_entity_id: "entity-01"
relations: [{predicate: "倾向前往", target: {entity_id: "entity-04"}}]
statement: "用户A更可能前往学校"
condition: "孩子在车上时"
```

孩子是条件参与者，主张仍然关于用户A。

只有来源明确表示一个稳定集体时才使用 Group。例如，来源明确表示的家庭可以承载“我们一家都喜欢去儿童餐厅”。朋友偶尔同行不会自动成为家庭成员。

如果原文说“A 和 B 都喜欢辣”，默认分别保存 A 和 B 的主张。一次同行或共同动作不推导共同偏好。

## 结构边界

来源、转述和显式时间保留在 `statement` 与 Raw Event 中。支撑时的环境保存在 `Evidence.context`，发生时间沿 Raw Event 回读。支撑次数从 Evidence 实时计算。

条件只保存自然语言。数据库索引和临时解析结果不能改变 MemoryItem 的逻辑含义。
