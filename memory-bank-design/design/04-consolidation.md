# 记忆巩固

## 设计状态

巩固处理单次 L2 抽取无法完成的跨 Session 归纳。

本文记录 `RepeatedBehaviorConsolidator` 原型的合同。它读取同一主体的多次直接行为，
生成 `status=candidate` 的重复行为规律。调用方必须显式调用：

```text
RepeatedBehaviorConsolidator.consolidate(subject_entity_id)
```

服务提供显式手动和自动巩固接口，但不自动决定触发时机，也没有定时任务。Candidate Discovery 的 13-task 三轮实验记录
在 `experiments/2026-07-31-candidate-then-condition-contract.md`。当前 fact-level
Condition 合同只在五个黄金 case 上完成三轮验证，结果见
`experiments/2026-07-31-consolidator-golden-cases.md`。

## 类层次

```text
Consolidator
  -> SubjectConsolidator
      -> RepeatedBehaviorConsolidator
```

`Consolidator` 定义显式巩固接口。`SubjectConsolidator` 把范围限制为一个主体。
`RepeatedBehaviorConsolidator` 只处理该 Entity 作为 `subject_entity_id` 的直接行为。

subject 必须是确认 Entity，且 `canonical_entity_id == id`。不存在的 ID、占位
Entity 和已合并到其他 Entity 的 ID 都直接失败。

## 候选来源

组件只读取：

- subject 的 `status=active` MemoryItem。
- 这些 MemoryItem 仍未进入任何 ConsolidationSupport 的 Evidence。
- Evidence 对应的完整 SourceSession。

MemoryItem 用于发现可能相同的行为。组件回读 Evidence 对应的完整 Session。第一阶段只
保留自身 Raw Event 包含驾驶员 Action 的 Evidence，并投影这些 Raw Event 的 Action 行；
第二阶段从完整 Session 提取动作前观察。candidate 不能成为下一轮归纳的直接来源。

subject 使用 Canonical Entity ID。旧 MemoryItem 仍引用已合并 Entity 时，Source Loading
通过 `canonical_entity_id` 联合读取，不改写原引用。

待巩固状态不持久化。MemoryStore 通过 anti-join 动态找到尚未被使用的 Evidence。
一次 Evidence 已经支持该类型巩固器的 candidate 后，后续同类型调用不再重复处理它。

当前实现只支持 `vehicle_session_v1`。它重新运行 VehicleRawDataParser 和
EpisodeBuilder，得到与写入时相同格式的完整 Session 文本。

## 两阶段调用

第一阶段读取符合来源条件的 MemoryItem，以及每条 Evidence 来源 Raw Event 的完整 Action
行，直接发现重复行为 candidate。它不读取 Context 状态快照。输入保留 Session 和 Action
事件边界，并在同一来源范围内把驾驶员 Action 切成编号短句。模型输出：

```json
{
  "candidates": [{
    "actions": ["完整动作一", "完整动作二"],
    "statement": "只描述用户反复做什么",
    "supports": [{
      "evidence": "E1",
      "action_clause_indices": [1, 2]
    }]
  }]
}
```

第一阶段不输出 `condition`、`condition_fragments` 或 `entity_mentions`。每个 candidate
至少引用两条独立 Evidence。同一 Evidence 可以支持多个不同 candidate。后处理按短句
编号恢复逐字 action fragments。

第二阶段逐 candidate 运行。它只读取固定 supports 的动作前观察，不读取第一阶段的
`actions` 或 `statement`。不同 candidate 可以并行归纳，全部成功后才进入写入。模型
输出：

```json
{
  "facts": [{
    "text": "一个共同事实",
    "clause_indices": [[1], [2]]
  }]
}
```

`clause_indices` 按输入中的 Evidence 顺序逐项对应。数量与固定 support 组不一致时直接
失败。后处理按 facts 顺序用分号拼成 Condition，并恢复 Evidence 标签和逐字 condition
fragments。

有系统 Action 时，后处理把它切成编号短句。没有系统 Action 时，后处理比较动作前后
Context，只提供发生变化的动作前短句。它再按模型返回的编号恢复逐字 condition
fragments。AND、OR、角色和数量只用自然语言表达，不增加条件结构。

## 来源连接

巩固不创建新 Evidence，也不复制完整 Evidence.context。它只新增：

```text
ConsolidationSupport {
  consolidator_type
  candidate_memory_item_id
  source_evidence_id
  fragments[]
}
```

`fragments` 合并该次支撑的 condition 和 action 原文片段。每个 candidate 至少需要
两条独立 Evidence。一个 Evidence 可以用不同片段支撑多个 candidates。

candidate 到来源 MemoryItem 的读取路径为：

```text
candidate -> ConsolidationSupport -> Evidence -> source MemoryItem
```

反向读取使用同一张表查询，不保存第二条反向边。支撑数量、来源 MemoryItem 和是否
还有待巩固 Evidence 都实时计算。

原 MemoryItem、Evidence、SourceSession 和 Raw Event 保持不变。

## 服务调用

手动接口同步巩固请求指定的一个 Canonical Subject。自动接口先动态统计当前 Bank 中每个
Canonical Subject 对应的待巩固 Evidence，过滤少于 5 条的 Subject，再按数量降序和
Entity ID 升序选择最多 `max_entities` 个。一次运行中每个 Subject 只尝试一次；成功和
`no_consolidation` 都继续处理下一个 Subject，真实错误停止运行。

同一 Bank 同时只允许一个手动或自动巩固。自动运行状态保存在服务进程内。取消是协作式
的：模型或 embedding 返回后、以及持久化事务开始前检查取消；当前实体未写入时放弃本次
结果，已经开始的写事务不打断。

## Entity

LLM 只输出 `actions` 完整动作文本。后处理把每项 action 写成 `predicate`，并令
`target=null`。

candidate 只继承其 supports 对应来源 MemoryItem 已有的 `entity_refs` 和 relation target
引用。表面词仍须逐字出现在最终 `statement`、`actions` 或 `condition` 中。确认 Entity
归一到 Canonical Entity；已有待确认 Entity 直接复用。相同表面词指向多个身份时明确
失败。该巩固器不识别新 mention，也不创建新的待确认 Entity。

## 校验和错误

`ConsolidationValidator` 只做确定性合同校验：

- Candidate 标签存在，同一 candidate 内不重复。
- action 和 condition 短句编号不重复且不越界。
- 每个 Condition fact 的 Evidence 集合与固定 candidate 完全一致。
- 每个 candidate 至少引用两条独立 Evidence。
- 每条 Support 同时包含非空 condition 和 action fragments。
- fragment 逐字存在于对应完整 Session 的来源文本，模板标签不算来源。
- 生成结果可以构造成普通 candidate MemoryItem。

Validator 不判断条件是否最小、阈值是否正确、关系语义是否准确，也不增加第三轮 LLM。

错误明确标出阶段：

```text
source_loading
candidate_discovery
condition_synthesis
fragment_validation
embedding
persistence
```

组件不重试、不跳过失败 candidate、不修补模型输出。所有 candidate、Support、
FTS 和 embedding 准备完成后一次写入。任一步失败都不留下部分巩固结果。

## task-011

`task-011` 的十条 history 都记录：会议中空调为手动模式、风量4档、噪声较高，
随后驾驶员切换自动模式并把风量调到1档。

预期生成一个包含“自动模式”和“风量1档”的 candidate。它引用十条来源 Evidence，
Condition 非空。五条 Positive 应适用，四条 Negative 应不适用。

这组数据中会议、手动模式、4档和高噪声共同变化。它可以验证 held-out 适用性，
不能证明唯一最小 Condition 或噪声阈值。

## 评估

当前 fact-level Condition 合同采用五个黄金 case，每个运行三次：

- 发现和归纳：`deepseek-v4-flash`，关闭思考，`temperature=0`。
- 不调用 Judge。
- 保存两阶段 prompt、模型输出和 Validator 结果。

黄金 case 包含动态目标、复合动作、共享 Evidence 多 candidate、相反方向和无重复行为。
通过条件及结果见 `experiments/2026-07-31-consolidator-golden-cases.md`。最终三轮的
Discovery 都满足预设语义和 support 断言；18 个 Condition candidate-run 与理想 facts、
数值范围和 indices 一致。第一阶段 Action 行投影的旧消融见
`experiments/2026-07-31-discovery-session-action-lines.md`。关键门 21/21、完整矩阵 39/39
分区正确并通过 Validator，第一阶段输入字符数降低 57.32%。旧矩阵没有使用当前
fact-level Condition 合同。这些结果不代表已经用新算法重新完成 117 条检索端到端
评测，也不能充分验证阈值边界或最小 Condition。

## 待办

- 设计何时由外部系统调用自动巩固接口；当前服务不决定触发时机。
- 设计大候选集超出上下文窗口时的选择和分批方法。
- 增加真实 Session 的多行为、阈值边界、复合动作部分适用和多 Entity 条件 case。
- 实现身份确认巩固器。
- 设计 candidate 修订、失效和转 active 的规则。
- 单独验证 readout 如何同时处理 candidate 和原无条件行为。
- 支持其他 SourceSession 格式。
