# MemoryItemExtractor

`MemoryItemExtractor` 读取一个完整 `EpisodeInput`，输出零条或多条候选主张：

```python
result = MemoryItemExtractor().extract(episode)
```

当前假设一个 Episode 能完整放入模型上下文。组件不切分长 Episode，不过滤 Episode 中的 Raw Event，也不做跨 Episode 归纳。第一次 LLM 调用生成完整主张结构和 Entity 候选；存在非空 relation target 时，第二次调用只判断哪些完整 target 值得建立 Entity 身份。组件随后严格校验并补充确定性 Context。

`EpisodeInput.events` 为空时，组件直接返回 `L2Result(items=[])`，不调用 LLM。

`EpisodeInput.required_actions` 是来源确认的必须输出。每项固定给出 `raw_event_id`、`subject`、`actor` 和 `action_code`。LLM 仍生成开放语义字段，不能只凭 Required Action 补写输入没有表达的内容。

## 输出

```text
L2Result {
  items: ExtractionItem[]
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

Relation {
  predicate: text
  target: text | null
}
```

`relations` 是有序数组。它保留同一主张中动作或关系的顺序。每个关系有自己的可选 `target`。完整语义以 `statement` 为准。

`relations` 必须保留区分具体主动操作的语义，不能将动作泛化为更宽泛的类型。
例如来源明确表达续播时，正文与关系都应保留续播含义，不能只在正文保留。

`statement` 和非空 `condition` 各自只能使用一行文本，不能包含 CR 或 LF。

同一个 ExtractionItem 中的关系必须共享主体、Condition、Evidence 和生命周期。仅仅出现在同一个 Episode 中不能合并。

## LLM 与确定性字段

第一次调用读取完整 Episode，输出 `subject`、`relations`、`statement`、可选 `condition`、候选 `entity_mentions` 和 `raw_event_ids`：

```json
{"items":[{"subject":"用户 usr-sim-06","relations":[{"predicate":"设置空调目标温度","target":"24℃"},{"predicate":"切换空调出风模式","target":"面部"}],"statement":"用户 usr-sim-06 在国网充电站·望京科技园充电时，将空调目标温度设置为24℃，并将出风模式切换至面部。","entity_mentions":["国网充电站·望京科技园"],"raw_event_ids":["record-000152"]}]}
```

第二次调用只读取按 ExtractionItem 和 relation 顺序排列的非空 target：

```json
{"targets":["国网充电站·望京科技园","24℃","主页"]}
```

它只返回值得建立 Entity 身份的索引。具体对象必须能够区分记忆内容、被其他记录再次引用，并且完整 target 整体指向单一对象。普通值、动作范围、通用类别、通用界面页面、路线和对象间关系不选。

第一次调用可以把完整 target 列为候选。组件先从候选中移除所有完整 target，再只按第二次调用的索引补回合格 target，并按原文位置生成最终 `entity_mentions`。因此 target 的最终 Entity 身份只由第二次调用决定。最终字段不包含 Entity ID，MemoryStore 使用精确文本匹配区分完整 target 和其他 mention。

组件根据 `raw_event_ids` 从当前 Episode 复制完整 Context：

```text
[record-id] EpisodeEventInput.context
```

多条依据按 `raw_event_ids` 顺序换行拼接。这个临时 `context` 在写入时归属 Evidence，不进入 MemoryItem。

第一次调用缺少的 `target`、`condition` 和 `entity_mentions` 分别由 schema 补成 `null`、`null` 和 `[]`。基础 `entity_mentions` 是候选元数据：组件删除非原文、完整 subject 和重复项，再按 statement/condition 中的位置排序。完整 target 是否进入最终 mentions 只由第二次调用决定；被选 target 不是主张原文时不建立 Entity mention，relation target 仍保留。

每个 Required Action 必须被同一 subject 的至少一个 item 覆盖，对应 `raw_event_id` 必须进入该 item。一个 item 可以同时引用其他背景 Event。required Action 存在时，`items=[]`、subject 不符或缺少 required Event 都直接失败。没有 Required Action 时，空结果合法。未知 Raw Event ID、非法 JSON 和 schema 不匹配直接失败。第二次调用的索引重复、乱序或越界也直接失败。组件不重试，不运行备用 Prompt。

## 抽取边界

- 抽取当前 Episode 直接支持、对当前用户世界有长期价值的事实、陈述、动作和明确偏好。Required Action 不再经过长期价值准入过滤。
- 一次行为不能直接升级为偏好。
- 普通系统日志、瞬时界面状态、固定响应和自动连锁结果不抽取。
- Action 已标注动作主体；非系统主体 detail 中属于该角色的主动操作必须完整保留。主动操作使用完成状态表达时，仍按该角色的一次动作处理。系统事件、车辆状态和设备状态不能改写成用户动作，也不能因为 Episode 中出现稳定用户引用就归因给该用户。Context 只说明环境和状态。
- 尽可能完成 Episode 内有证据的简单共指消解，不强制解决证据不足的多人指称。
- detail 表达动作，payload 表达事件发生后的状态；不能把每个 payload 字段自动拆成独立动作。
- predicate 使用输入语言的简短具体文本，并包含动作涉及的领域对象；target 保存具体对象、动作作用范围或结果值。二者都不设置枚举。
- 常识只帮助理解输入，不能补写不存在的因果或结果。

## Case

五组车辆人工标注和六组小型合同 case 位于 `data/test/memory_item_extractor/`。`expected-extractions.json` 与 `contract-cases.json` 中的 expected 都是人工 oracle，不能由被测组件生成或覆盖。合同 case 验证明确定义的 Condition、同一 Episode 内的独立重复行为、系统状态不能归因成用户动作，以及混合 actor Episode 中的明确非系统动作不能被系统事件一并过滤。

单元测试把人工 oracle 作为一份完整 stub 响应，验证严格 schema、默认值、Raw Event ID、mention 校验和 Context 生成。它不调用模型，也不能证明 Prompt 的语义质量。真实抽取效果以固定 case 的重复 probe 和人工复核为准，实验记录位于 `memory-bank-design/experiments/`。

真实模型运行命令：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python scripts/run_memory_item_extractor_cases.py
```

运行会从仓库根目录 `.env` 和 `config/llm.toml` 读取 DeepSeek 配置，固定执行三轮。输出记录两种 system Prompt、每次调用的 user Prompt、严格校验后的响应和最终结果。没有非空 target 的 case 只调用一次，其他 case 调用两次。任一 case 失败时，脚本先输出已经完成的调用和错误，再以非零状态停止，不重试或继续下一 case。

当前三轮证据保存在 `memory-bank-design/experiments/artifacts/`。真实结果中的 predicate、target、主体共指、车窗粒度和最小 Evidence 仍有波动，不能把严格 schema 通过等同于语义全部通过。

## 已知缺陷

第一次调用抽取候选 `entity_mentions` 时仍可能漏掉主张中的具名 Entity。第二次调用只解决完整 relation target 的 Entity 判断，不能补回其他实体。

target 分类 Prompt 在固定 14 个边界 target 上连续三轮精确通过，但不能据此保证所有领域 target 都分类正确。通过证据见 `2026-07-28-memory-item-extractor-single-target-entity.md`；此前失败边界也保留在同目录实验记录中。

一次真实 `session-0101` 调用得到：

```json
{
  "subject": "用户 usr-sim-06",
  "relations": [
    {"predicate": "设置空调目标温度", "target": "24℃"},
    {"predicate": "切换空调出风模式", "target": "面部"}
  ],
  "statement": "用户 usr-sim-06 在国网充电站·望京科技园充电时，将空调目标温度设为24℃，并将出风模式切换到面部。",
  "condition": null,
  "entity_mentions": ["国网充电站·望京科技园"],
  "raw_event_ids": ["record-000152"],
  "context": "[record-000152] 2026-03-02T08:19:00+08:00，用户 usr-sim-06。空调状态发生变化。……"
}
```

该示例中的主张字段由第一次调用生成。两个 target 都不是 Entity，因此第二次调用返回空索引，最终 `entity_mentions` 只保留充电地点。

## 车辆差分输入

车辆链路使用`build_vehicle_claim_episode(session, episode)`生成LLM输入视图，然后调用`extract(episode, claim_episode=claim_episode)`。来源适配代码维护Session内状态池；Extractor不读取车辆私有字段。两个Episode只能context不同。首调用读取差分视图，引用校验与Evidence.context始终使用完整episode；TargetEntity流程不变，不增加模型调用。来源无独立视图时仍直接读取episode。

车辆状态池内部仍按原始字段和值比较；LLM输入使用Parser的FIELD_RULES、render_leaf与render_payload_value生成自然语言。保留前后值的来源ID和时间。同名字段标签附原路径，未覆盖的字符串值保留原值；不新增映射词典、不删事件、不改Action及Required Action，完整Episode继续用于Evidence。
