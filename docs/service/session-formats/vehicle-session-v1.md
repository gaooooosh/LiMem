# vehicle_session_v1

写入和格式校验接口一次只接受一个已经结束的车辆 Session JSON。不能提交包含
`sessions[]` 的完整数据包。顶层必须是 JSON object。所有固定结构的对象都严格
拒绝未知字段；未知的 Device Tree 或 trigger payload 路径也会失败，包括未知路径
上的空对象。

写入前可以用用户 API key 调用：

```http
POST /v1/limem/session-formats/vehicle-session-v1/validate
X-API-Key: <user-api-key>
Content-Type: application/json
```

请求体直接放 Session JSON。校验成功返回：

```json
{
  "valid": true,
  "source_format": "vehicle_session_v1",
  "session_id": "session-0001",
  "record_count": 2
}
```

该接口只执行 RawDataParser，不调用 LLM 或 embedding，不创建记忆库，也不保存
Session。校验失败返回 `422 invalid_session_format` 和字段路径。校验成功不表示相同
`session_id` 还可以写入某个记忆库。

## 最小有效示例

```json
{
  "session_id": "session-0001",
  "vehicle_id": "veh-001",
  "user_id": "usr-001",
  "records": [
    {
      "record_id": "record-0001",
      "timestamp": "2026-07-29T10:00:00+08:00",
      "type": "heartbeat",
      "trigger": null,
      "device_tree": {}
    }
  ]
}
```

## Session

| 字段 | 类型 | 要求 |
| --- | --- | --- |
| `session_id` | string | 必填、非空、单行；在单个记忆库内唯一 |
| `vehicle_id` | string | 必填、非空 |
| `user_id` | string | 必填、非空 |
| `records` | array | 必填，至少一条 Record |

## Record

| 字段 | 类型 | 要求 |
| --- | --- | --- |
| `record_id` | string | 必填；Session 内唯一 |
| `timestamp` | string | ISO-8601，必须包含时区 |
| `type` | string | `heartbeat` 或 `trigger` |
| `trigger` | object/null | heartbeat 必须为 null；trigger 必须为对象 |
| `device_tree` | object | 必填；可以为空对象 |

Record 数组顺序就是事件顺序。

### Session 边界 Record

服务接受 `eventType=SessionStartEvent` 和 `eventType=SessionEndEvent`。它们可以缺失、
单独出现、重复或位于 Record 数组任意位置。两类边界 Record 必须使用 `type=trigger`，
并设置 `payload={}`、`device_tree={}`。服务校验后过滤边界 Record，不把它们写入
Episode、Evidence、embedding 或检索索引。

校验响应中的 `record_count` 包含边界 Record。只包含边界 Record 的 Session 可以
通过校验和写入，写入结果的 `memory_items_created` 为 0。

## Trigger

```json
{
  "eventType": "MediaEvent",
  "startTime": "2026-07-29T10:01:00+08:00",
  "endTime": null,
  "source": "媒体播放",
  "actor": "driver",
  "payload": {"cabin":{"media":{"play_status":"playing"}}},
  "detail": "开始播放音乐。",
  "relatedAction": "media.play"
}
```

普通输入 Trigger 只允许以下 `eventType`：

```text
BodyAccessEvent
ClimateAirEvent
ConnectivityPhoneEvent
DialogueEvent
EnergyChargingEvent
EnvironmentLocationEvent
LightingWiperEvent
MaintenanceEvent
MediaEvent
NavigationEvent
OccupancyEvent
PerceptionSafetyEvent
ScheduleEvent
ScreenEvent
SeatComfortEvent
VehicleMotionEvent
```

`AgentEvent` 只用于输出审计，不能触发输入 Record。`actor` 接受
`driver/passenger/rear/system/agent/unknown`。`unknown` 是合法的非空枚举值；`""`
不属于正式合同。LiMem 与 ProAgent 使用相同 actor 枚举。

Trigger 的 8 个字段都必须出现：

| 字段 | 类型 | 要求 |
| --- | --- | --- |
| `eventType` | string | 使用上面的固定事件类型 |
| `startTime` | string | ISO-8601，必须包含时区 |
| `endTime` | string/null | 必须包含时区，且不能早于 `startTime` |
| `source` | string | `导航记录/媒体播放/舱内摄像头/屏幕/车机对话/车辆状态/环境感知/助手/查询结果` |
| `actor` | string | 使用上面的固定 actor |
| `payload` | object | 恰好一个非空设备子树 |
| `detail` | string | 必填 |
| `relatedAction` | string | 必填；可以为空字符串 |

payload 必须同时满足四项约束：路径属于正式设备协议；根节点属于 `eventType` 的设备
域；同一路径存在于当前 Record 的 Device Tree；两处 JSON 值完全相同。服务不裁剪、
改名、补值或修复 payload。

## Device Tree 合同

Device Tree 对齐 li-proagent 的[《设备及其状态协议》](https://github.com/MagicGeekSquad/li-proagent/blob/main/docs/%E8%AE%BE%E5%A4%87%E5%8F%8A%E5%85%B6%E7%8A%B6%E6%80%81%E8%AF%B4%E6%98%8E.md)。
服务固定登记 `542` 个标量叶和以下 `3` 个数组叶：

```text
seat.slot_order
cabin.dialogue.passengers
sensing.cabin_camera.objects
```

14 个顶层总成为 `chassis/power/body/hvac/seat/lighting/wiper/cabin/sensing/`
`safety/connectivity/navigation/schedule/maintenance`。子对象可以不出现；出现的字段、
实例、类型、范围和枚举必须与正式协议一致。未知路径直接失败。

车窗规范路径为 `body.window.rowN.left/right.open_pct`。座椅只包含
`row1.left/right`、`row2.left/right` 和 `row3.left/middle/right`，不建立二排中座。

## Trigger payload 叶子

Trigger payload 可以使用正式设备协议中的任意状态叶，但只能包含一个事件设备子树。
事件域固定为：

| EventType | payload 根域 |
| --- | --- |
| `NavigationEvent` | `navigation` |
| `MediaEvent` | `cabin.media` / `cabin.audio` |
| `DialogueEvent` | `cabin.dialogue` |
| `ConnectivityPhoneEvent` | `connectivity` |
| `OccupancyEvent` | `cabin.occupancy` / `seat` |
| `ScreenEvent` | `cabin.screen` |
| `VehicleMotionEvent` | `chassis` |
| `EnergyChargingEvent` | `power` |
| `BodyAccessEvent` | `body` |
| `SeatComfortEvent` | `seat` |
| `ClimateAirEvent` | `hvac` |
| `LightingWiperEvent` | `lighting` / `wiper` |
| `PerceptionSafetyEvent` | `sensing` / `safety` |
| `EnvironmentLocationEvent` | `sensing.env` / `sensing.location` / `cabin.cabin_env` |
| `MaintenanceEvent` | `maintenance` / `chassis.wheel` |
| `ScheduleEvent` | `schedule` |

## 类型、值和数组

- `int` 只接受 JSON 整数。服务不会把 `10.0` 转成 `10`。
- `float` 接受有限 JSON 数字。每个数值字段按正式协议检查上下界。
- 数值未知值和布尔未知值使用 `null`。Trigger payload 与 Device Tree 同路径都为
  `null` 时合法，并在 Raw Event payload 中明确写成“未知”。
- 字符串未知值使用 `""`。枚举只接受正式 token，服务不做大小写、别名或
  snake_case 修复。
- 媒体 `category` 是自由文本并原样输出。
- `front_outlet_mode` 与 `airflow_mode` 独立校验。
- `schedule` 是局部快照，可以只提供一个或多个正式字段。
- `seat.slot_order` 必须严格等于七个规范座位的固定顺序。
- `cabin.dialogue.passengers` 的每项必须是完整 `seat.*` 节点。
- `sensing.cabin_camera.objects[]` 的每项必须恰好包含 `object_id/object_class/seat/`
  `confidence/held_by`，并分别校验类型和值。

旧的 `row1_left` 风格车窗路径、`speaker_seat="driver"`、字符串 `null` 和未知路径
上的空对象都直接失败。

格式错误响应会在 `details[].path` 中指出 Record 位置。
