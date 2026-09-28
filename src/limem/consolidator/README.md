# Consolidator

Consolidator 处理单次 Extraction 无法完成的跨 Session 归纳。当前类层级为：

```text
Consolidator
  -> SubjectConsolidator
      -> RepeatedBehaviorConsolidator
      -> EntityFamiliarityConsolidator（TODO）
```

当前只有 `RepeatedBehaviorConsolidator` 已实现。它读取同一主体的多次直接行为，生成带
Condition 的 `status=candidate` MemoryItem，并用 `ConsolidationSupport` 连接每条来源
Evidence。`EntityFamiliarityConsolidator` 只预留类和接口；调用会明确抛出
`NotImplementedError`，尚不推断熟悉关系。

组件只在显式调用时运行：

```python
from limem.consolidator import RepeatedBehaviorConsolidator

result = RepeatedBehaviorConsolidator(memory_store).consolidate(
    subject_entity_id
)
```

当前没有自动触发或定时任务。服务提供显式手动和自动调用接口。实现只支持
`vehicle_session_v1`。

## 整体流程

```text
subject_entity_id
  -> Source Loading
  -> Candidate Discovery             一次 LLM 调用
  -> Condition Synthesis             每个 candidate 一次 LLM 调用，可并行
  -> Fragment Validation             确定性
  -> MemoryItem / Support Materialize 确定性
  -> Embedding
  -> Persistence
```

任一阶段失败都会抛出带阶段名的 `ConsolidationError`。组件不重试、不跳过失败 candidate，
也不修补模型输出。全部 candidate 完成后才写入。

Source 投影、Discovery candidate 和 Condition facts 都是单次调用内的临时结构，不会
持久化。当前流程没有 Opportunity 结构。最终只保存 candidate MemoryItem、
ConsolidationSupport 和 embedding。

两阶段拆分的是两个不同判断：Discovery 先确定“哪些 Evidence 表达同一种重复动作”，
Condition Synthesis 再回答“这组 Evidence 共同出现了哪些动作前事实”。第二阶段不读取
Discovery 生成的 Condition，因为 Discovery 根本不生成 Condition；它只接收已固定的
support 组、候选完整有序 actions 及动作前观察。确定性后处理再把 Discovery
candidate 与 Condition facts 合并。这样 Condition 错误不会反过来改变行为分组，也可以
按 candidate 独立定位和并行调用。

## Source Loading

组件读取：

- subject 的 `status=active` 直接 MemoryItem；
- 这些 MemoryItem 中尚未进入任何 ConsolidationSupport 的 Evidence；
- Evidence 指向的完整 SourceSession。

组件重新运行 `VehicleRawDataParser` 和 `EpisodeBuilder`。Discovery 输入包含直接
MemoryItem 的 statement、原 condition、relations，以及 Evidence 来源 Raw Event 的 Action
行；只有自身 Raw Event 包含驾驶员 Action 的 Evidence 才进入 Discovery，动作编号也只从
这些 Raw Event 生成。它不读取完整 Session 的环境 Context。Condition Synthesis 再从完整
Session 中提取驾驶员动作前观察。

candidate 不能成为下一轮巩固的直接来源。Evidence 一旦支持当前类型巩固器生成的
candidate，后续同类型调用不会再次把它列为待巩固来源。

## 阶段一：Candidate Discovery

当前运行基线使用结构化 few-shot 提示词，包含购买图书、解锁平板并继续编辑文档、
投影亮度提高4三个示例。模型仍自行分组并输出动作及编号引用。
2026-09-22 realism 配对测试中，结构合法输出由12/21提升至21/21，最终合格规则
仍为0/14。按用户决定将该版本接入后续运行；这不表示规则巩固质量已通过验证。
详见实验记录（研发仓库历史记录：`../../../memory-bank-design/experiments/2026-09-22-qwen-realism-fewshot.md`；本发行仓库不附带）。

目标是回答两个问题：

1. 存在哪些重复行为？
2. 每个行为由哪些 Evidence 和驾驶员 Action 短句支持？

模型一次读取全部观察，输出零个或多个 action-only candidate：

```json
{
  "candidates": [{
    "actions": ["设置导航目的地为充电站", "开启前往充电站的导航"],
    "statement": "用户设置导航目的地为充电站并开启导航",
    "supports": [
      {"evidence": "E1", "action_clause_indices": [1, 2]},
      {"evidence": "E2", "action_clause_indices": [1, 2]}
    ]
  }]
}
```

这一阶段不输出 Condition 或 Entity mention。具体名称不同可以归为同一行为，但必须
保留共同的功能对象类别。方向、动作对象、变化量或动作序列不同的行为不能合并。

当复合动作只在部分 Evidence 中共同出现，而其中各动作分别获得更多独立支持时，只
保留覆盖更广的独立 candidates。若这些动作在每条支持 Evidence 中都共同出现，则保持
为一个复合 candidate。

## 阶段二：Condition Synthesis

每个 candidate 单独运行。输入包含候选完整有序动作、固定 Evidence 组的动作前观察
及最少支持数量。模型从固定组内选择超过半数的 Evidence 支持条件；重复观察保留，
以输入 Evidence 标签计数，不按 Session 或动作事件去重。

动作前存在多条系统 Action 时，按 Session 原始顺序展开所有短句并连续编号，不限制
系统观察数量。模型输入与引用恢复共用该编号；动作后的系统 Action 不进入条件输入。
无前置系统 Action 时仍使用原有 Context 比较路径。本步骤不合并前后状态或去重。


条件结果使用显式 Evidence 标签，不依赖数组位置：

```json
{
  "facts": [{
    "text": "会议进行中",
    "supports": [
      {"evidence": "E1", "clause_indices": [1]},
      {"evidence": "E3", "clause_indices": [2]},
      {"evidence": "E4", "clause_indices": [1]}
    ]
  }]
}
```

例如固定组有4条 Evidence，上述3条达到严格多数；8条需要至少5条，4/8不通过。
每个 fact 都须达到严格多数。facts 按 AND 拼接时，支持所有 facts 的 Evidence
交集也须达到严格多数。最终只为交集来源保存 ConsolidationSupport；未选来源不伪装
成完整条件的支撑。按原候选顺序恢复标签，按各 fact 的显式标签恢复原文片段。
数值范围使用所选支持来源的完整范围。旧位置式 clause_indices 结构不再接受。

## 确定性边界

`ConsolidationValidator` 检查：

- Evidence 标签存在且不重复；
- candidate 至少有两条独立 Evidence；
- 每个 Condition fact 及完整组合条件的支持数均超过固定组的一半；
- action 和 condition clause indices 非空、不重复且不越界；
- fragments 逐字存在于允许的 Session 来源文本；
- 最终结果可以构造成普通 candidate MemoryItem。

Validator 不判断分组语义、Condition 是否最小、数值范围是否合理，也不判断 fact 文本
是否真的由 fragments 蕴含。语义质量由固定 case 和人工复核判断，不能把“Validator
通过”等同于“归纳正确”。

最终 candidate 的 relations 由 `actions` 按顺序生成，target 为 `null`。Entity 引用只
继承支持来源中已有且仍出现在 candidate 文本里的引用；该组件不识别新 Entity mention。

“主驾座椅通风”发生跨 Episode 身份冲突时，`vehicle_identity.py` 核对库内全部相关
引用和直接来源，要求同一 vehicle_ref、驾驶员 seat.setVentilation 动作、明确原文和
主驾通风 payload。该身份指车辆功能位置。只确认尚未确认的占位实体；其他冲突仍失败。
`_materialize` 返回 candidate、Support 和身份确认计划，不写库；`_persist` 原子保存
三者及向量。旧 MemoryItem 与 Evidence 保持不变。


## 黄金 Case

| Case | 输入特征 | 理想结果 |
| --- | --- | --- |
| `simple-dynamic-target` | 三次导航到不同充电站 | 一个 candidate；保留“充电站”和“设置目的地 + 开启导航”；Condition 包含电量范围 |
| `compound-action-envelope` | 三次会议中切自动模式并调至1档 | 一个复合 candidate；Condition 保留会议、手动4档和噪声范围 |
| `shared-evidence-multiple-candidates` | E1/E2 同时暂停媒体和开勿扰，E3/E4 分别只做一项 | 两个 candidates；共享 E1/E2；两个 Condition 分别排除另一行为的前态 |
| `opposite-directions-near-miss` | 两次提高8、两次降低6 | 两个 candidates；方向和变化量不能混合 |
| `no-repeated-behavior` | 四条互不重复行为 | 空 candidates；不调用 Condition |

### Case：动态目标

三次观察都在车辆驶离停车区、电量为 16% 至 19%、没有补能导航时发生。驾驶员分别把
三个具体充电站设为目的地并开启导航。

理想 Discovery 抽象具体站名，但保留共同类别“充电站”和完整动作序列。理想 Condition
包含驶离停车区、电量 16% 至 19%、没有进行中的补能导航。

这个 case 防止两类错误：按具体站名拆成多个行为，或把“充电站”过度抽象成无类别的
“目的地”。

### Case：共享 Evidence

```text
E1: 会议前，媒体播放中，勿扰未开启 -> 暂停媒体 + 开启勿扰
E2: 会议前，媒体播放中，勿扰未开启 -> 暂停媒体 + 开启勿扰
E3: 会议前，媒体播放中             -> 暂停媒体
E4: 会议前，勿扰未开启             -> 开启勿扰
```

理想 Discovery 生成“暂停媒体”和“开启勿扰”两个 candidates。E1、E2 可以同时支持两者，
但不能额外生成只由 E1、E2 支持的冗余复合 candidate。

理想 Condition 分别取事实交集：

- 暂停媒体：会议将在 1 至 3 分钟内开始；媒体正在播放。
- 开启勿扰：会议将在 1 至 3 分钟内开始；勿扰模式未开启。

这个 case 直接促成了 fact-level support。旧输出会把两个前态同时写入两个 Condition，
并生成不属于 E4 的 clause index。

## 当前验证结果与限制

最终固定 Prompt 下，五个 case 各运行三轮。15 次 Discovery 满足预设语义和 support
断言；18 个 Condition candidate-run 与理想 facts、数值范围和 indices 一致。

随后使用相同配置完成真实两阶段联合评测。15/15 个 run 的实际 Discovery support
结构正确，18/18 个实际 Condition 调用与对应理想 facts 和 indices 精确一致，所有
Validator 均通过。12/15 个 Discovery 输出与黄金文本不逐字一致；差异是“反复”字样和
同义动作措辞，人工复核未发现动作语义变化。

这些结果只证明五个单组件 case 的两阶段内存链路。随后启动了 13-task、117-request
检索端到端评测。正式首轮有 9/13 个 task 完成。修复 Evidence 动作来源和 Condition
输出合同后，4 个失败 task 的针对性复跑全部完成；其中 `task-003`、`task-009` 通过
Action Judge，`task-001`、`task-002` 仍有 Discovery 动作语义遗漏。当前没有在修复后
重跑全部 13 个 task，也没有验证 Entity 消歧或 Opportunity。

设计合同见 `memory-bank-design/design/04-consolidation.md`，完整实验过程见
`memory-bank-design/experiments/2026-07-31-consolidator-golden-cases.md` 和
`memory-bank-design/experiments/2026-07-31-consolidator-joint-golden-cases.md`。检索端到端结果
见 `memory-bank-design/experiments/2026-07-31-consolidator-retrieval-e2e.md`。

## 离线行为Agent基线

[behavior_agent](behavior_agent/README.md)保存`behavior-react-20260923`基线，从重复操作归纳行为偏好Y。该模块独立调用，保留候选、来源查询与核对轨迹，未接入服务巩固流程或MemoryStore。当前测得13项提交5项，其中3项完整正确；条件归纳尚未接入。
