# RawDataParser

## 作用

`RawDataParser` 一次读取一个已经结束的来源 Session。它严格校验 Session 和全部
Record，过滤 Session 边界 Record，把其他 Record 转成 `RawEvent`。

Parser 完整保留来源 Record，也输出 EpisodeBuilder 可以直接读取的确定性字段。它
不分组 Episode，不判断偏好、意图或长期记忆价值，也不调用 LLM。

```text
来源 Session JSON
  -> RawDataParser
  -> SessionInput { session_ref, record_count, raw_events }
  -> EpisodeBuilder
```

## 接口

```python
from limem.raw_data_parser import VehicleRawDataParser

session = {...}
parsed = VehicleRawDataParser().parse(session)
```

父类只定义：

```text
source: str
format_description: str
parse(session_json: JsonObject) -> SessionInput
```

`parse` 不修改输入。非法输入直接失败，不返回部分结果。当前不使用注册表、factory
或动态插件。

## Session 和边界

`SessionInput.record_count` 保存来源 `records` 总数。普通 Raw Event 的
`session_position` 保存来源数组位置。

`SessionStartEvent` 和 `SessionEndEvent` 只划定来源范围。它们可以缺失、单独出现、
重复或位于任意位置，但必须是 Trigger Record，且 `payload={}`、
`device_tree={}`。Parser 校验后过滤它们。只包含边界的 Session 会得到空
`raw_events`。

## Vehicle 协议

`VehicleRawDataParser` 对齐 li-proagent 的[《设备及其状态协议》](https://github.com/MagicGeekSquad/li-proagent/blob/main/docs/%E8%AE%BE%E5%A4%87%E5%8F%8A%E5%85%B6%E7%8A%B6%E6%80%81%E8%AF%B4%E6%98%8E.md)。
`vehicle_protocol.py` 固定登记 `542` 个标量叶和三个数组叶：

```text
seat.slot_order
cabin.dialogue.passengers
sensing.cabin_camera.objects
```

字段路径、实例、类型、数值范围和枚举都使用明确规则。未知路径直接失败，未知路径
上的空对象也不例外。
服务不做别名解析、大小写修复、默认值替代、数值截断或未知字段透传。

三种数组分别执行结构校验：

- `slot_order` 必须严格等于七个规范座位的固定顺序。
- `passengers[]` 的每项必须是完整 `seat.*` 节点。
- `objects[]` 的每项必须恰好包含 `object_id/object_class/seat/confidence/held_by`。

正式值规则包括：JSON `int` 不接受 `10.0`；数值和布尔未知值使用 `null`；字符串
未知值使用 `""`；枚举 token 必须精确匹配。

车窗只接受 `body.window.rowN.left/right.open_pct`。`speaker_seat` 只接受完整
`seat.*` 节点。媒体 `category` 是自由文本并原样输出。`front_outlet_mode` 与
`airflow_mode` 独立校验。局部快照中的 `schedule` 可以只提供一个或多个正式字段。

## Trigger 合同

普通输入接受 16 类设备事件：

```text
NavigationEvent MediaEvent DialogueEvent ConnectivityPhoneEvent
OccupancyEvent ScreenEvent VehicleMotionEvent EnergyChargingEvent
BodyAccessEvent SeatComfortEvent ClimateAirEvent LightingWiperEvent
PerceptionSafetyEvent EnvironmentLocationEvent MaintenanceEvent ScheduleEvent
```

`AgentEvent` 只用于输出审计，不能触发输入 Record。actor 接受
`driver/passenger/rear/system/agent/unknown`。`unknown` 是合法的非空枚举值；`""`
不属于正式合同。LiMem 与 ProAgent 使用相同 actor 枚举。

普通 Trigger payload 必须满足：

1. 恰好包含一个非空设备子树。
2. 根节点属于当前 EventType 的设备域。
3. 每个叶路径都属于正式设备协议。
4. 同一路径存在于当前 Record 的 Device Tree。
5. payload 与 Device Tree 的 JSON 值完全相同。

Trigger payload 中的每个字段都进入 `RawEvent.payload`。旧的 28 个字段继续使用原
固定输出名；新增字段使用规范设备路径作为输出名。`null` 明确写成“未知”，空字符串
明确写成“空值”。

## RawEvent 输出

来源字段始终存在：

| 字段 | 含义 |
| --- | --- |
| `raw_event_id` | 来源 Record ID |
| `session_ref` | 来源 Session ID |
| `session_position` | Record 在 Session 数组中的位置 |
| `user_ref` / `vehicle_ref` | 来源身份引用 |
| `source` | 当前为 `vehicle_logs` |
| `occurred_at` | 带时区的来源时间 |
| `record_type` | 定时触发或状态发生变化 |
| `event` | 可以直接成句的事件描述 |
| `raw_payload` | 未改写的完整来源 Record |

以下字段有内容时才输出：

| 字段 | 内容 |
| --- | --- |
| `actor/action/detail/payload` | Trigger 的主体、动作和变化后状态 |
| `location/environment` | 位置与车内外环境 |
| `vehicle` | 底盘与运动状态 |
| `occupants/seats` | 乘员与座椅状态 |
| `media/screen/dialogue` | 座舱交互状态 |
| `navigation/energy/schedule/climate` | 导航、动力、日程和空调状态 |
| `windows/body` | 车窗与其他车身状态 |
| `lighting/wiper` | 灯光与雨刮状态 |
| `perception/safety` | 感知与安全状态 |
| `connectivity/maintenance` | 连接与维护状态 |

同一设备域中已有人工表达的字段继续使用人工表达；其余正式字段按固定中文标签、
枚举和值单位输出。`None` 表示来源没有提供对应信息。标准 `model_dump()` 会省略
这些空字段。

空调分区目标温度保留来源区域。后排字段 `hvac.zone.rear.target_temp_c`
输出“后排目标温度”，不能与整体或前排目标温度混写。

## 验证数据

- `data/test/raw_data_parser/selected-sessions.json` 保存 20 个 history Session。
- `data/test/raw_data_parser/cases/` 保存 5 个已人工复核的黄金 case。
- `tests/raw_data_parser/test_vehicle_protocol.py` 固定最新路径、数组、事件、actor、
  值和组合约束。
- 远端提交 `03ce41b8259a68bffbb5417c5b5c0c1c75e83610` 的
  `2026-08-24-overfit-v3-device-protocol-v1-reviewed` 共 424 个 Session、704 条
  Record，全部通过 parser。其中 220 个 history Session 共 500 条 Record。

## 车辆抽取输入视图

`vehicle_state_pool.build_vehicle_claim_episode(session, episode)`属于车辆来源适配。它在Parser和EpisodeBuilder之后，从已保存的Raw Event来源快照计算Session内差分，只替换抽取视图context。首观测初始化，显式null和空串保留，缺失保留为最近观测且不伪写为当前值。固定保留原话、媒体/导航等对象信息，不筛除事件，不删除动作原文。完整episode不变，用于Evidence和写入。

车辆状态池内部仍按原始字段和值比较；LLM输入使用Parser的FIELD_RULES、render_leaf与render_payload_value生成自然语言。保留前后值的来源ID和时间。同名字段标签附原路径，未覆盖的字符串值保留原值；不新增映射词典、不删事件、不改Action及Required Action，完整Episode继续用于Evidence。
