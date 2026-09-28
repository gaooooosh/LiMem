# 记忆巩固

记忆巩固读取一个记忆库中已经写入的 MemoryItem 和 Evidence，归纳跨 Session 才能确认的
规律，并写入新的 candidate MemoryItem。当前服务只提供显式调用，不会定时触发，也不会在
Session 写入后自动触发。

当前只支持 `repeated_behavior`，对应 `RepeatedBehaviorConsolidator`。

## 手动巩固一个主体

```http
POST /v1/limem/{memory_bank_id}/consolidations
X-API-Key: <user-api-key>
Content-Type: application/json
```

```json
{
  "subject_entity_id": "entity_driver",
  "consolidator_type": "repeated_behavior"
}
```

`subject_entity_id` 必须是记忆库内已经确认的 Canonical Subject。调用同步处理这个主体当前
尚未被同类型巩固使用的 Evidence。响应示例：

```json
{
  "memory_bank_id": "bank_...",
  "subject_entity_id": "entity_driver",
  "consolidator_type": "repeated_behavior",
  "pending_evidence_count": 10,
  "outcome": "consolidated",
  "candidate_memory_item_ids": ["memory_..."]
}
```

`outcome=no_consolidation` 表示本次没有发现满足合同的重复行为。它不是接口失败。
`candidate_memory_item_ids` 给出本次新增的 candidate MemoryItem。

## 自动选择主体

```http
POST /v1/limem/{memory_bank_id}/auto-consolidations
X-API-Key: <user-api-key>
Content-Type: application/json
```

```json
{
  "consolidator_type": "repeated_behavior",
  "max_entities": 10
}
```

服务实时统计每个 Canonical Subject 尚未被 `repeated_behavior` 使用的 Evidence。它只选择
至少有 5 条待巩固 Evidence 的主体，再按 Evidence 数量降序、Entity ID 升序处理，最多
尝试 `max_entities` 个主体。接口返回 `202` 和 `run_id`。

使用以下接口查询或取消运行：

```text
GET  /v1/limem/{memory_bank_id}/auto-consolidations/{run_id}
POST /v1/limem/{memory_bank_id}/auto-consolidations/{run_id}/cancel
```

运行状态包括 `running`、`cancel_requested`、`completed`、`failed` 和 `cancelled`。
`results` 保留每个已处理主体的结果。`failed` 时，`failure` 明确给出主体、失败阶段和错误
信息，运行随即停止。取消不会中断正在进行的模型或 embedding 请求；它会在这些请求返回后、
以及写事务开始前生效。

同一记忆库同时只允许一个手动或自动巩固。巩固期间不能删除该记忆库。自动运行状态只保存在
当前服务进程中，服务重启后不能继续查询旧 `run_id`。

## RepeatedBehaviorConsolidator

### 输入

组件只读取指定主体作为 `subject_entity_id` 的直接 MemoryItem。候选范围排除已经通过
`ConsolidationSupport` 被同类型巩固使用的 Evidence。组件再沿每条 Evidence 回读原始
Session，只保留包含驾驶员 Action 的来源。

逻辑输入包含：

- 指定 Canonical Subject；
- 该主体的直接 MemoryItem；
- 每条 MemoryItem 的待巩固 Evidence；
- Evidence 所在 Session 中的环境状态和驾驶员 Action。

手动接口不要求 5 条 Evidence。5 条阈值只用于自动选择主体。重复行为 candidate 至少需要
两条独立 Evidence 支撑。

### 工作原理

第一阶段发现多次发生的同类动作，输出一个或多个 action candidate，并选择每个 candidate
实际使用的 Evidence 和动作片段。第一阶段不归纳 Condition。

第二阶段对每个固定 candidate 独立归纳 Condition。Condition 表示这条规律的硬适用条件，
即高置信度的 `IF Condition THEN Action`。组件只保留所有 Support Evidence 都具备的事实，
并恢复实际使用的条件片段和动作片段。

确定性 Validator 检查 Evidence 引用、片段边界和支持关系。随后组件生成 embedding，并用一个
事务写入全部 candidate 和 ConsolidationSupport。任何阶段失败都不会留下部分巩固结果。

### 产生的效果

每条规律产生一个新的 candidate MemoryItem。它包含归纳后的 `statement`、`condition`、
主体引用和从来源 MemoryItem 继承的关系。组件不复制或新建 Evidence。每条
ConsolidationSupport 连接 candidate 与一条原 Evidence，并只保存实际使用的原文片段。

检索可以按 candidate 自身的文本和 embedding 直接命中它。检索路径命中来源 MemoryItem
时，Retriever 会沿 ConsolidationSupport 把对应 candidate 追加到该路径。Support 扩展不参与
候选排名和 `limit`。

当前检索链路不做反向展开：candidate 本身进入检索路径时，不会再沿 Support 追加来源
MemoryItem。MemoryStore 仍可通过 ConsolidationSupport 反查来源，这个能力目前只供内部读取。

## ProAgent 端到端结果

巩固后运行在每个 task 写入 10 个 history Session，再显式执行一次 `repeated_behavior`
自动巩固。13 个 task 全部产生 candidate。随后完成 117 个 eval Session、234 条
empty/real 双臂结果。

| 指标 | 未巩固基线 | 巩固后 | 变化 |
| --- | ---: | ---: | ---: |
| Real LabelAcc | 54.7% (64/117) | 61.5% (72/117) | +6.8 pp，+8 条 |
| Real Positive LabelAcc | 70.8% (46/65) | 72.3% (47/65) | +1.5 pp，+1 条 |
| Real Negative LabelAcc | 34.6% (18/52) | 48.1% (25/52) | +13.5 pp，+7 条 |
| Real ActAgree | 44.6% (29/65) | 63.1% (41/65) | +18.5 pp，+12 条 |
| Real OverDisturb | 65.4% (34/52) | 51.9% (27/52) | -13.5 pp，少 7 条误触发 |
| Delta+ | 41.5 pp | 61.5 pp | +20.0 pp |

这些数字确认了两个运行各自的结果，不构成严格的巩固器消融。两次运行同时改变了 LiMem
实现、是否执行巩固和外部模型响应。Empty arm 的 LabelAcc 也从 47/117 变为 43/117，说明
存在模型运行间波动。因此不能把全部增量归因于巩固器。要判断巩固器的因果增益，还需要在
当前代码、相同服务和相同配置下补跑不执行巩固的对照组。
