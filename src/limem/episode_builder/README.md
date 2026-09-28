# EpisodeBuilder

## 作用

`EpisodeBuilder` 把一个完整 `SessionInput` 组织成 L2 一次读取的 `EpisodeInput`。

当前固定输出一个 Episode，不切窗、不删除 Raw Event、不做语义去重，也不调用 LLM。没有 Raw Event 的 Session 输出一个 events 为空的 Episode。

```text
SessionInput / RawEvent[]
  -> EpisodeBuilder
  -> EpisodeInput[]
```

## 接口

```python
from limem.episode_builder import EpisodeBuilder

episodes = EpisodeBuilder().build(session_input)
```

公开接口只有：

```text
EpisodeBuilder.build(session: SessionInput) -> list[EpisodeInput]
```

`EpisodeBuilder` 没有父类、配置、窗口策略、registry 或 factory。

## 输入合同

`SessionInput` 必须满足：

1. `session_ref` 非空。
2. `record_count` 至少为 1。
3. `raw_events` 可以为空。
4. 每条 Raw Event 的 `session_ref` 与 Session 一致。
5. `raw_event_id` 在 Session 内唯一。
6. `session_position` 严格递增且小于 `record_count`。

`RawEvent.event` 必须是能够直接成句的可读文本。所有保留文本以及 `payload` 的键和值都必须包含实际内容，`occurred_at` 有值时必须带时区。`actor`、`detail` 和 `payload` 必须同时存在或同时为空。来源映射属于 RawDataParser adapter，不属于 EpisodeBuilder。

## 输出合同

```text
EpisodeInput {
  episode_id: text
  events: EpisodeEventInput[]
  required_actions: RequiredActionInput[]
}

EpisodeEventInput {
  raw_event_id: text
  context: text
  action?: text
}

RequiredActionInput {
  raw_event_id: text
  subject: text
  actor: text
  action_code: text
}
```

Episode ID 固定为 `${session_ref}:episode:0`。heartbeat 没有 Action，序列化时省略 `action`。

### Context

Context 按以下顺序拼接非空字段：

```text
occurred_at + user_ref
event
location
environment
vehicle
occupants
media
screen
navigation
energy
schedule
climate
windows
seats
dialogue
body
lighting
wiper
perception
safety
connectivity
maintenance
```

每段直接使用 RawDataParser 给出的可读文本。EpisodeBuilder 不添加“环境”“媒体”等标签，不解释字段含义。各段移除末尾中文句号后再用 `。` 连接。

### Action

Action 只使用：

```text
actor + detail + payload
```

模板为：

```text
{actor}：{detail}。变化后状态：key 为 value，key 为 value。
```

`payload` 保存事件发生后的状态值，不表示每个字段都相对前态发生变化。EpisodeBuilder 保持其键和值顺序，不比较前态，也不把字段拆成动作。Action 文本不使用 `vehicle_ref`、`source`、`record_type`、原始 `RawEvent.action` 或 `raw_payload`。

### Required Action

EpisodeBuilder 把 `actor != "系统"` 且存在 `RawEvent.action` 的 Event 标记为 Required Action。`raw_event_id`、`actor` 和 `action_code` 直接来自 Raw Event；`subject` 固定为 `用户 <user_ref>`。这类 Event 缺少 `user_ref` 时直接失败。系统 Action 和没有 action code 的 Event 不进入列表。

Required Action 按 Event 顺序排列，ID 必须属于当前 Episode，不能重复或乱序。它只向 L2 声明动作必须被保留，不生成 relation、statement、condition 或 Entity。

## 实际输出案例

下面是黄金 case `session-0261` 的固定输出。第一条是 heartbeat，第二条是媒体恢复动作：

```json
[
  {
    "episode_id": "session-0261:episode:0",
    "required_actions": [
      {
        "raw_event_id": "record-000392",
        "subject": "用户 usr-sim-14",
        "actor": "驾驶员",
        "action_code": "scene.restoreAfterMeeting"
      }
    ],
    "events": [
      {
        "raw_event_id": "record-000391",
        "context": "2026-03-02T18:05:00+08:00，用户 usr-sim-14。定时触发。位置：望京科技园B座停车场；地理类型：office。车外6℃；舱内23℃；噪声38dB；照度160lux。P挡；车速0km/h；上电状态：就绪。成员1人：主驾。QQ音乐中周杰伦的《稻香》已暂停；类型：音乐；音量64；音区：全车；音频焦点模式：normal。中控屏亮度8级；显示主页；HMI应用：腾讯会议；HMI媒体：《腾讯会议·产品周会》已暂停；HMI说明：会议已结束；勿扰模式仍开启；会前媒体《稻香》处于暂停状态。导航未开启。没有下一日程。空调开启；自动模式；风量1档；向面部出风；前排出风模式：face。勿扰模式开启；说话成员：主驾。蓝牙连接：是；车载热点：否；网络状态：在线。麦克风健康：ok。"
      },
      {
        "raw_event_id": "record-000392",
        "context": "2026-03-02T18:09:00+08:00，用户 usr-sim-14。媒体状态发生变化。位置：望京科技园B座停车场；地理类型：office。车外6℃；舱内23℃；噪声38dB；照度160lux。P挡；车速0km/h；上电状态：就绪。成员1人：主驾。QQ音乐正在播放周杰伦的《稻香》；类型：音乐；音量64；音区：全车；音频焦点模式：normal。中控屏亮度8级；显示主页；HMI应用：QQ音乐；HMI媒体：《稻香》正在播放；HMI说明：车载会议上下文已建立。导航未开启。没有下一日程。空调开启；自动模式；风量1档；向面部出风；前排出风模式：face。勿扰模式关闭；说话成员：主驾。蓝牙连接：是；车载热点：否；网络状态：在线。麦克风健康：ok。",
        "action": "驾驶员：勿扰模式已关闭，会前暂停的《稻香》已恢复播放。变化后状态：media_status 为 正在播放，media_title 为 稻香。"
      }
    ]
  }
]
```

完整固定结果见：

- `data/test/episode_builder/expected-episodes.json`
- `data/test/episode_builder/manifest.json`

## 当前限制

- 长 Session 如何生成多个 Episode 尚未设计。
- Episode membership 不表示一次发生，也不表示一组共同 Evidence。
- Context 和 Action 是派生文本，不写入当前持久结构。
