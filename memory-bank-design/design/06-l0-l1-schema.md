# L0/L1 原始与分组层 Schema

## 职责

- L0 保存原始 Session 中的 Raw Event。
- RawDataParser 一次读取一个完整且已经结束的来源 Session JSON。
- RawDataParser 完整保存来源 Record，并输出确定性的简短字段。
- EpisodeBuilder 把一个 Session 的全部 Raw Event 组织成一个 Episode。
- L0 保存 Raw Event 在 Session 中的来源顺序。
- L1 保存 Episode 的 Raw Event 成员。
- L2 每次只读取一个 Episode。

Episode 是输入窗口。它不表示一次意图、动作、事实或观察。一个 Episode 可以包含多次发生，Episode 数量不能表示发生次数。

## 数据流

```text
来源 Session JSON
  -> RawDataParser
  -> SessionInput / RawEvent[]
  -> EpisodeBuilder
  -> EpisodeInput[]
  -> L2 逐个 Episode 抽取
```

RawDataParser 只处理来源格式：

1. 严格校验 Session 和 Record 外壳。
2. 完整保留原始 Record。
3. 保存来源 ID、发生时间和 Session 内顺序。
4. 把来源直接表达的事件、动作和设备状态转换成可以直接阅读的简短字段。

RawDataParser 不判断偏好、意图和长期记忆价值。它不删除 Raw Event，也不切分 Episode。

EpisodeBuilder 只使用 RawDataParser 已经给出的可读字段。它不读取 `raw_payload`，不删除 Raw Event，不理解来源私有 JSON，也不做 L2 语义抽取。当前一个 Session 生成一个 Episode。长 Session 切分尚未设计。

## 输入与输出

```text
SessionInput {
  session_ref: text
  raw_events: RawEvent[]
}

RawEvent {
  raw_event_id: text
  session_ref: text
  session_position: integer
  user_ref?: text
  vehicle_ref?: text
  source: text
  occurred_at?: timestamp with timezone
  record_type: text
  event: text
  actor?: text
  action?: text
  detail?: text
  payload?: key-value
  location/environment/vehicle/occupants?: text
  media/screen/navigation/energy?: text
  schedule/climate/windows/seats/dialogue?: text
  raw_payload: json
}

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

EpisodeRef {
  episode_id: text
  raw_event_ids: text[]
}
```

`RawEvent.raw_payload` 是未改写的来源 Record。`event` 必须能够直接成句。其余可选字段是 RawDataParser 从来源直接生成的确定性表达。所有输出文本以及 `payload` 的键和值必须包含实际内容。`occurred_at` 有值时必须带时区。`actor`、`detail` 和 `payload` 必须同时存在或同时为空。当前车辆映射见 `src/limem/raw_data_parser/README.md`。

`EpisodeInput.context` 按固定顺序拼接 `occurred_at`、`user_ref`、`event` 和非空设备状态。`action` 只拼接 `actor`、`detail` 和 `payload`。EpisodeBuilder 不读取 `vehicle_ref`、`source`、`record_type`、原始 `action` 或 `raw_payload`。

数组顺序就是 L2 读取顺序。RawDataParser 保持成员在原 Session 中的相对顺序。当前每个 Raw Event 恰好进入一个 Episode。

EpisodeInput 不包含参与者、摘要、情境特征、处理状态或版本号。

`EpisodeRef.raw_event_ids` 按 `session_position` 排列。

当前 `RawEvent.raw_event_id` 保存来源 Record ID。下方持久结构的 `raw_event.id` 是内部 UUID。两者如何映射还没有决定。本次不实现保存和重读接口。

## 持久结构

```sql
CREATE TABLE raw_event (
    id              uuid PRIMARY KEY,
    session_ref     text NOT NULL,
    session_position integer NOT NULL CHECK (session_position >= 0),
    user_ref        text,
    vehicle_ref     text,
    source          text NOT NULL,
    occurred_at     timestamptz,
    received_at     timestamptz NOT NULL,
    record_type     text NOT NULL,
    raw_payload     jsonb NOT NULL,
    UNIQUE (session_ref, session_position)
);

CREATE TABLE episode (
    id uuid PRIMARY KEY
);

CREATE TABLE episode_event (
    episode_id   uuid NOT NULL REFERENCES episode(id),
    raw_event_id uuid NOT NULL REFERENCES raw_event(id),
    PRIMARY KEY (episode_id, raw_event_id)
);
```

### Raw Event

- `id` 是稳定引用。
- `session_ref` 保存来源给出的 Session 归属。
- `session_position` 保存 Raw Event 在 SessionInput 中的位置。UUID 和时间戳不能可靠恢复来源顺序。
- `user_ref` 和 `vehicle_ref` 只保存来源明确给出的引用，不做身份推断。
- `occurred_at` 只保存来源声称的发生时间。来源没有提供时保持空值。
- `received_at` 是系统收到记录的时间，不能冒充发生时间。
- `raw_payload` 保存未改写的来源 Record。RawDataParser 生成的简短字段可以从它重建，不在持久层重复保存。

### Episode

- `episode` 保存稳定 ID。
- `episode_event` 保存成员。读取顺序来自成员 Raw Event 的 `session_position`。
- membership 只表示 EpisodeBuilder 把记录放入同一 Episode，不表示这些记录共同支持一条主张。
- 同一 Episode 的全部 Raw Event 必须来自同一个 Session。

RawDataParser 生成的简短字段以及 EpisodeBuilder 生成的 Context 和 Action 不永久保存。首次抽取直接使用当前输出。之后如何从持久记录重建来源 Session JSON，等持久化 ID 映射确定后再设计。

## 最小接口

```text
RawDataParser.parse(source_session_json) -> SessionInput
EpisodeBuilder.build(session: SessionInput) -> EpisodeInput[]
```

保存、重读和 Episode membership 接口尚未确定。

## 约束

1. Session 边界由来源给出。固定频率回传不会创建新 Session。
2. L2 只读取 EpisodeInput；当前一个 Episode 包含同一 Session 的全部 Raw Event。
3. Episode 边界不能证明两段内容属于同一次发生。
4. Episode 不能充当 Evidence 或发生次数。
5. EpisodeBuilder 不删除或跳过 Raw Event。
6. RawDataParser 不能改变同一 Session 内 Raw Event 的相对顺序。
7. 更换来源格式时只修改对应 RawDataParser；EpisodeBuilder 不增加来源映射。
8. 当前设计不保证 RawDataParser 或 EpisodeBuilder 修改后逐字复现旧的派生文本。
