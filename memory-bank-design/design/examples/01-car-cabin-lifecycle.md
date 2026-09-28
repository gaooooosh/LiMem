# 黄金样例一：车舱记忆最小通路

## 目标

用车机数据验证：

```text
Session
  -> RawDataParser
  -> EpisodeBuilder
  -> Episode
  -> ExtractionItem
  -> MemoryItem + Evidence
  -> 巩固产生条件候选
```

本 case 只验证已经确定的行为，不指定模型、提示词、召回算法或分数。

## 原始输入

样例包含三个已经结束的用车 Session。

| Session | Raw Event | 来源内容 |
|---|---|---|
| S1 | R01 | 主驾：“理想同学，我最近身体有点不舒服，总觉得有点累。” |
| S1 | R02 | 车机：“好的，您最近要多注意休息。” |
| S1 | R03 | 摄像头：主驾表情疲惫并在说话；副驾无人。 |
| S1 | R04 | 屏幕：健康关怀对话页面；播放停止；未收藏。 |
| S2 | R05 | 主驾：“你知道长城一共多长吗？” |
| S2 | R06 | 车机回答长城长度。 |
| S2 | R07 | 摄像头：主驾和副驾在场。 |
| S2 | R08 | 屏幕：长城知识卡片。 |
| S3 | R09 | 副驾：“开始导航。” |
| S3 | R10 | 车机：“好的，杭州大厦走起！” |
| S3 | R11 | 屏幕：杭州大厦，开始导航。 |
| S3 | R12 | 导航记录：从当前位置开始导航到杭州大厦。 |
| S3 | R13 | 屏幕：杭州大厦，导航中。 |
| S3 | R14 | 摄像头：主驾和副驾在场。 |
| S3 | R15 | 主驾：“给我播放首音乐。” |
| S3 | R16 | 车机：“让我们一起欣赏张叶蕾的《还是分开》。” |
| S3 | R17 | 摄像头：主驾说话，副驾未说话。 |
| S3 | R18 | 屏幕：网易云音乐《还是分开》，停止。 |
| S3 | R19 | 媒体记录：播放张叶蕾的《还是分开》。 |
| S3 | R20 | 屏幕：《还是分开》，播放中。 |
| S3 | R21 | 摄像头：主驾和副驾在场。 |

每条 Raw Event 还保存来源提供的 `occurred_at`、`source` 和完整 `raw_payload`。没有来源依据的字段保持未知。

## Episode 输出

当前 EpisodeBuilder 为每个 Session 输出一个 Episode：

| 引用名 | `episode_id` | 有序成员 |
|---|---|---|
| E1 | `S1:episode:0` | R01, R02, R03, R04 |
| E2 | `S2:episode:0` | R05, R06, R07, R08 |
| E3 | `S3:episode:0` | R09, R10, R11, R12, R13, R14, R15, R16, R17, R18, R19, R20, R21 |

E1 至 E3 只用于文档引用，不表示三次语义事件。

E1 固定 `user_ref=session-user-01`。对话 adapter 把原话放入 `detail`，同时输出 `payload.utterance`。实际 L2 输入为：

```yaml
episode_id: S1:episode:0
events:
  - raw_event_id: R01
    context: "2026-07-20T07:04:36+08:00，用户 session-user-01。对话状态发生变化。"
    action: "主驾：理想同学，我最近身体有点不舒服，总觉得有点累。变化后状态：utterance 为 理想同学，我最近身体有点不舒服，总觉得有点累。"
  - raw_event_id: R02
    context: "2026-07-20T07:04:36+08:00，用户 session-user-01。对话状态发生变化。"
    action: "车机：好的，您最近要多注意休息。变化后状态：utterance 为 好的，您最近要多注意休息。"
  - raw_event_id: R03
    context: "2026-07-20T07:04:39+08:00，用户 session-user-01。乘员状态发生变化。主驾表情疲惫并在说话；副驾无人。"
  - raw_event_id: R04
    context: "2026-07-20T07:04:42+08:00，用户 session-user-01。屏幕状态发生变化。健康关怀对话页面；播放停止；未收藏。"
```

Raw Event 保存各自在 Session 中的位置，Episode 保存 membership。Episode 持久层据此恢复相同顺序，不保存上面的 Context 和 Action。L2 只把实际支撑 ExtractionItem 的 Event Context 复制到 Evidence。

下文使用 `CTX[R01, R02]` 表示 MemoryItemExtractor 生成的 context。它不是字段中的字面值，而是以下确定性文本：

```text
[R01] <R01 的 EpisodeEventInput.context>
[R02] <R02 的 EpisodeEventInput.context>
```

Event 按 Episode 顺序排列。ExtractionItem 和由它写入的 Evidence 使用同一个 `CTX[...]`。

以下固定 case 直接验证多 Event context，不使用占位符：

```yaml
episode:
  events:
    - {raw_event_id: C01, context: "第一段环境。"}
    - {raw_event_id: C02, context: "仅为邻近背景。"}
    - {raw_event_id: C03, context: "第三段环境。"}

llm_raw_event_ids: [C01, C03]
expected_context: "[C01] 第一段环境。\n[C03] 第三段环境。"
```

C02 没有直接支持主张，因此不能进入 expected context。这个 case 同时固定了 Episode 顺序、`[raw_event_id]` 前缀和换行符。

## Case 1：直接自述

E1 的 L2 输出：

```yaml
items:
  - subject: "本次主驾用户"
    relations:
      - predicate: "自述健康状况"
    statement: "本次主驾用户自述近期身体不适并感到疲惫"
    condition: null
    entity_mentions: []
    context: CTX[R01]
    raw_event_ids: [R01]
```

R03 是邻近摄像头观察，不能因为位于同一 Episode 就合入第一人称自述的 Evidence。

写入层为本次主驾创建占位 Entity `entity-01`。来源没有提供跨 Session 身份依据，因此不能只按“主驾”角色复用其他 Session 的 Entity：

```yaml
entity-01:
  name: "S1 本次主驾用户"
  aliases: []
  types: [Person]
  canonical_entity_id: null

M1:
  subject_entity_id: entity-01
  relations:
    - predicate: "自述健康状况"
  statement: "本次主驾用户自述近期身体不适并感到疲惫"
  condition: null
  entity_refs: []
  status: active

EV1:
  memory_item_id: M1
  raw_event_ids: [R01]
  context: CTX[R01]
```

## Case 2：不产生长期记忆

E2 只是一次通用知识问答。L2 输出：

```yaml
items: []
```

R05 至 R08 仍保留在 L0/L1，但不生成 MemoryItem。

## Case 3：角色和行为边界

E3 输出：

```yaml
items:
  - subject: "本次副驾用户"
    relations:
      - predicate: "开启导航"
        target: "杭州大厦"
    statement: "一名身份未确认的副驾用户发起前往杭州大厦的导航"
    condition: null
    entity_mentions: []
    context: CTX[R09, R12]
    raw_event_ids: [R09, R12]
```

它不能写成“主驾发起导航”或“用户到达杭州大厦”。

E3 还输出两个独立抽取项：

```yaml
items:
  - subject: "本车车机"
    relations:
      - predicate: "播放歌曲"
        target: "《还是分开》"
    statement: "车机播放了张叶蕾的《还是分开》"
    condition: null
    entity_mentions: ["张叶蕾"]
    context: CTX[R19]
    raw_event_ids: [R19]

  - subject: "本次主驾用户"
    relations:
      - predicate: "请求播放"
        target: "音乐"
    statement: "本次主驾用户请求播放音乐"
    condition: null
    entity_mentions: []
    context: CTX[R15]
    raw_event_ids: [R15]
```

写入后形成三个 active MemoryItem 和三条 Evidence。相关 Entity fixture 为：

```yaml
entity-02: {name: "S3 本次副驾用户", aliases: [], types: [Person], canonical_entity_id: null}
entity-03: {name: "杭州大厦", aliases: [], types: [Place], canonical_entity_id: entity-03}
entity-04: {name: "本车车机", aliases: [], types: [Device], canonical_entity_id: entity-04}
entity-05: {name: "《还是分开》", aliases: [], types: [MusicTrack], canonical_entity_id: entity-05}
entity-06: {name: "张叶蕾", aliases: [], types: [Person], canonical_entity_id: entity-06}
entity-07: {name: "S3 本次主驾用户", aliases: [], types: [Person], canonical_entity_id: null}
```

`entity-02` 和 `entity-07` 是不同的当次角色占位 Entity：

```yaml
M2:
  subject_entity_id: entity-02
  relations:
    - predicate: "开启导航"
      target: {entity_id: entity-03}
  statement: "一名身份未确认的副驾用户发起前往杭州大厦的导航"
  condition: null
  entity_refs: []
  status: active

M3:
  subject_entity_id: entity-04
  relations:
    - predicate: "播放歌曲"
      target: {entity_id: entity-05}
  statement: "车机播放了张叶蕾的《还是分开》"
  condition: null
  entity_refs:
    - {text: "张叶蕾", entity_id: entity-06}
  status: active

M4:
  subject_entity_id: entity-07
  relations:
    - predicate: "请求播放"
      target: "音乐"
  statement: "本次主驾用户请求播放音乐"
  condition: null
  entity_refs: []
  status: active

EV2: {memory_item_id: M2, raw_event_ids: [R09, R12], context: CTX[R09, R12]}
EV3: {memory_item_id: M3, raw_event_ids: [R19], context: CTX[R19]}
EV4: {memory_item_id: M4, raw_event_ids: [R15], context: CTX[R15]}
```

E3 不能产生“主驾喜欢张叶蕾”或“主驾喜欢《还是分开》”。车机选曲和播放结果不等于用户偏好。

## Case 4：同类行为重复发生

后续四个 Session 各包含一条来源完整的驾驶请求：

| Raw Event | 来源内容和 metadata |
|---|---|
| R23 | 15:10，认证用户A，activity=driving，“播放点音乐” |
| R24 | 16:20，认证用户A，activity=driving，“放首歌吧” |
| R25 | 14:40，认证用户A，activity=driving，“给我播放音乐” |
| R26 | 17:00，认证用户A，activity=driving，“来点音乐” |

L2 对每条记录分别输出一个 ExtractionItem。四项的核心内容相同：

```yaml
subject: "用户A"
relations:
  - predicate: "请求播放"
    target: "音乐"
statement: "用户A在下午驾驶时请求播放音乐"
condition: null
entity_mentions: []
context: CTX[Rxx]
raw_event_ids: [Rxx]
```

`Rxx` 分别替换为 R23、R24、R25、R26。四项各自引用对应 Raw Event。写入层复用一个行为 MemoryItem，但保留四条 Evidence：

本 case 已确认四条来源中的认证用户A对应同一个 Entity：

```yaml
entity-08: {name: "用户A", aliases: [], types: [Person], canonical_entity_id: entity-08}
```

```yaml
M5:
  subject_entity_id: entity-08
  relations:
    - predicate: "请求播放"
      target: "音乐"
  statement: "用户A在下午驾驶时请求播放音乐"
  condition: null
  entity_refs: []
  status: active

EV23: {memory_item_id: M5, raw_event_ids: [R23], context: CTX[R23]}
EV24: {memory_item_id: M5, raw_event_ids: [R24], context: CTX[R24]}
EV25: {memory_item_id: M5, raw_event_ids: [R25], context: CTX[R25]}
EV26: {memory_item_id: M5, raw_event_ids: [R26], context: CTX[R26]}
```

四个 Episode 不能直接充当四次发生。四次发生来自四条独立行为依据。

## Case 5：巩固产生条件候选

巩固回读 EV23 至 EV26 后可以提出：

```yaml
M6:
  subject_entity_id: entity-08
  relations:
    - predicate: "偏好音乐"
      target: "音乐"
  statement: "用户A可能更倾向于听音乐"
  condition: "下午驾驶时"
  entity_refs: []
  status: candidate

CS23: {candidate_memory_item_id: M6, source_evidence_id: EV23, fragments: ["15:10，activity=driving", "播放点音乐"]}
CS24: {candidate_memory_item_id: M6, source_evidence_id: EV24, fragments: ["16:20，activity=driving", "放首歌吧"]}
CS25: {candidate_memory_item_id: M6, source_evidence_id: EV25, fragments: ["14:40，activity=driving", "给我播放音乐"]}
CS26: {candidate_memory_item_id: M6, source_evidence_id: EV26, fragments: ["17:00，activity=driving", "来点音乐"]}
```

每条 Raw Event 自身同时包含下午时间、驾驶状态和请求内容。巩固通过四条
ConsolidationSupport 保留独立来源和实际使用的片段，不创建或复制 Evidence。
四次直接行为仍由 M5 的 EV23 至 EV26 分别保存。

这个 case 只支持 candidate。它不能证明用户在其他时间不喜欢音乐，也不能证明“下午驾驶”是唯一条件。

## Case 6：明确的雨天偏好

新增 Raw Event：

```text
R31 | speaker=user-a | 用户A：“下雨开车时，我一般喜欢听萧敬腾。”
```

L2 可以直接输出：

```yaml
subject: "用户A"
relations:
  - predicate: "偏好音乐"
    target: "萧敬腾"
statement: "用户A通常喜欢听萧敬腾的歌"
condition: "下雨且正在驾驶时"
entity_mentions: []
context: CTX[R31]
raw_event_ids: [R31]
```

写入后：

```yaml
entity-09: {name: "萧敬腾", aliases: [], types: [Person], canonical_entity_id: entity-09}
```

```yaml
M7:
  subject_entity_id: entity-08
  relations:
    - predicate: "偏好音乐"
      target: {entity_id: entity-09}
  statement: "用户A通常喜欢听萧敬腾的歌"
  condition: "下雨且正在驾驶时"
  entity_refs: []
  status: active

EV31: {memory_item_id: M7, raw_event_ids: [R31], context: CTX[R31]}
```

R31 明确表达完整 IF-THEN，因此不需要等待多次行为。

## Case 7：多人条件

新增 Raw Event：

```text
R32 | speaker=user-a | 用户A：“小明和小红都在车上时，我通常先去学校。”
```

L2 输出：

```yaml
subject: "用户A"
relations:
  - predicate: "倾向前往"
    target: "学校"
statement: "用户A通常先前往学校"
condition: "小明和小红都在车上时"
entity_mentions: ["小明", "小红"]
context: CTX[R32]
raw_event_ids: [R32]
```

用户A仍是唯一主要主体。小明和小红是条件参与者。这个同车组合不创建 Group。

本 case 固定二者已经完成身份解析：

```yaml
entity-11: {name: "小明", aliases: [], types: [Person], canonical_entity_id: entity-11}
entity-12: {name: "小红", aliases: [], types: [Person], canonical_entity_id: entity-12}
```

完整写入结果为：

```yaml
M8:
  subject_entity_id: entity-08
  relations:
    - predicate: "倾向前往"
      target: "学校"
  statement: "用户A通常先前往学校"
  condition: "小明和小红都在车上时"
  entity_refs:
    - {text: "小明", entity_id: entity-11}
    - {text: "小红", entity_id: entity-12}
  status: active

EV32: {memory_item_id: M8, raw_event_ids: [R32], context: CTX[R32]}
```

如果巩固以后把条件泛化成“用户A的任意一个孩子在车上时”，新候选的 `entity_refs` 为空，但条件仍保留“用户A的孩子”这一关系范围。

## Case 8：同一过程中的复合动作

新增两个按顺序发生的 Raw Event。R33 明确记录会议已结束、勿扰模式仍开启且会前暂停的《稻香》仍处于暂停状态。R34 明确记录同一用户“先关闭勿扰模式，随后恢复播放《稻香》”。

MemoryItemExtractor 可以把同一主体、同一过程和同一组依据支持的动作合并：

```yaml
subject: "用户A"
relations:
  - predicate: "关闭勿扰模式"
  - predicate: "恢复播放歌曲"
    target: "《稻香》"
statement: "会议结束后，用户A关闭勿扰模式，随后恢复播放会前暂停的《稻香》"
condition: null
entity_mentions: []
context: CTX[R33, R34]
raw_event_ids: [R33, R34]
```

写入结果保留相同关系顺序：

```yaml
entity-13: {name: "《稻香》", aliases: [], types: [MusicTrack], canonical_entity_id: entity-13}

M9:
  subject_entity_id: entity-08
  relations:
    - predicate: "关闭勿扰模式"
    - predicate: "恢复播放歌曲"
      target: {entity_id: entity-13}
  statement: "会议结束后，用户A关闭勿扰模式，随后恢复播放会前暂停的《稻香》"
  condition: null
  entity_refs: []
  status: active

EV33:
  memory_item_id: M9
  raw_event_ids: [R33, R34]
  context: CTX[R33, R34]
```

“会议结束后”只表达来源支持的顺序。本 case 不声称会议结束导致关闭勿扰模式或恢复播放。

## 验收断言

1. Episode 输入中的每段 Context 和 Action 都能定位到一个 Raw Event。
2. Episode 持久层能够按 Raw Event 的 `session_position` 恢复原始输入顺序。
3. E1 只用 R01 支撑自述，不自动合并 R03。
4. E2 输出零条。
5. E3 不声称到达，也不产生音乐偏好。
6. R23 至 R26 产生四个抽取项和四条行为 Evidence。
7. 巩固产生一条自然语言 condition 的 candidate，并用四条 ConsolidationSupport 连接四条独立 Evidence。
8. 明确条件偏好可以由 L2 直接写成 active。
9. 多个具体孩子能够绑定实体；通用角色不会被错误缩窄。
10. 多关系 ExtractionItem 和 MemoryItem 保留来源支持的动作顺序。
11. Context 写入 Evidence，不进入 MemoryItem；时间、Session 和 Episode 归属沿 Raw Event 回读。
