# 行为偏好Agent

固定基线：`behavior-react-20260923`。核心代码保存2026-09-23已采纳的ReAct行为归纳版本。该模块独立调用，未注册到service pipeline，未部署。

## 任务与输入

从同一主体的重复操作中归纳行为偏好Y。适用条件X留到下一阶段，输出中的condition为null。

`run(client, data)`接收已分组的操作环境：

- `task`：案例或任务标识。
- `subject`：主体。
- `evidence_count`：分组前Evidence数量。
- `records`：按顺序排列的操作。每条包含`id`、`evidence`、`session_id`、`event_id`、`before`、`operation`和`source_variants`。
- `operation`含`code`、`detail`和`parameters`。参数保留`field`、`before_present`、`before`、`after_present`、`after`、`requested`及来源提供的`delta`。
- `source_variants`保留这次操作关联的原始Evidence卡片，包括`evidence`和动作前`before`。

同一Session和动作事件对应的Evidence在进入模块前合并为一次操作。不同操作使用不同B编号。参数中文名称定义在environment.py，未知字段直接失败。输入准备仍由调用方完成。

## 工作规范

初始提供两次操作。每轮只执行一个动作，总计最多六轮。

| 阶段 | 可用工具 | 作用 |
| --- | --- | --- |
| propose | propose、放弃结束 | 保存完整行为、支持编号和一个疑点 |
| inspect | inspect、放弃结束 | unread读未读操作；source读指定来源；match按明确字段、位置和值筛选未读操作 |
| review | review | 只依据已读操作重新归纳行为，输出引用与缺失信息，替换候选并保留旧版本 |
| ready | finish | 结束当前任务 |

核对引用至少两次操作、包含初始两条之外的来源且缺失信息为空时进入ready。字段、编号和类型由每轮Schema限定。查询为空不会确认候选。六轮耗尽保留state与trace，结果状态为decision_budget_exhausted。

来源在6000 token预算内逐步加入。QwenClient固定使用Qwen3-1.7B、temperature=0、seed=0、非思考、512输出token及JSON Schema；每次调用请求和响应保存在client.calls。请求失败直接抛出，失败记录同样保留。

## 调用

```python
from limem.consolidator.behavior_agent import QwenClient, run

client = QwenClient("http://127.0.0.1:30033")
result = run(client, data)
```

返回status、candidate、decisions、read_records、state、trace。candidate包含behavior、condition、supports和open_question。该模块不写MemoryStore；后续条件归纳、实体绑定和保存尚未接入。

## 查询工具优化

`query_agent.run(client, data)`保留上述行为阶段，只将inspect改为选择查询编号。环境列出查询内容、实际命中编号和数量；不列空查询，相同结果的参数比较只保留一项。模型选择后仍执行现有查询并进入review。

该版本改善查询选择，仍可能生成不完整的行为。它独立于包级 `run` 的冻结行为基线。服务未接入此模块，条件归纳、实体绑定和存储仍未接入。本发行仓库不包含评测脚本、基线结果 JSON 或回放数据。
