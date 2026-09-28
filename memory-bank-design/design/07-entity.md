# 实体设计

## 目标

Entity 为不同 MemoryItem 提供稳定的身份引用。它只回答不同记忆中的对象是否为同一个对象。

Entity 不表示一次文本中的称呼、Session 角色、参与者列表或实体关系。

## Schema

```text
Entity {
  id: text
  name: text
  aliases: text[]
  types: text[]
  canonical_entity_id: text | null
}
```

### `id`

无语义、唯一、稳定的内部 ID。ID 由写入层生成，不由 LLM 生成。文档中的 `entity-01` 只表示一个不携带身份含义的样例 ID。

### `name`

用于显示的名称，不要求唯一。占位实体可以使用能够回到当前来源的描述，例如“S3 本次副驾用户”。

### `aliases`

同一实体稳定使用的其他名称，例如“小明”“明明”“王小明”。`name` 和 `aliases` 可以匹配一个或多个 Entity，不能单独证明两个指称是同一身份。

同一个名称或别名可以属于多个 Entity。系统不能因为字符串相同就合并实体。

当一个表面称呼已经明确绑定到同一 Entity，并且它是稳定名称时，写入层把它加入 `aliases`。代词、Session 角色、关系描述和通用角色不能加入。

以下内容不保存为别名：

- `我`、`他`等代词。
- `本次主驾`、`当前副驾`等 Session 角色。
- `张三的孩子`等关系描述。
- `用户A的任意一个孩子`等通用角色。
- `妈妈`、`我的儿子`等依赖说话人的相对称呼。

代词和 Session 角色结合当前 Episode 处理。关系描述通过 MemoryItem 查询。通用角色保留在 `statement` 或 `condition` 中。

### `types`

开放的实体类别，例如 `Person`、`Place`、`Device`、`Group`。驾驶者、乘客、朋友和孩子属于当前角色或实体关系，不能作为 Entity 的临时类型。

### `canonical_entity_id`

该字段同时表示身份是否已经确认，以及确认后的最终 Entity：

| 值 | 含义 |
|---|---|
| `null` | 当前无法确认身份的占位 Entity |
| 等于自己的 `id` | 已确认是独立 Entity |
| 指向另一个 Entity | 已确认与目标是同一身份 |

等于自身只表示已确认独立。除此之外，合并结果不能形成多步链或不同 Entity 之间的循环；被合并 Entity 必须直接指向最终 Entity。

## 写入时解析实体

当前 `MemoryStore` 只实现稳定 `user_ref` 绑定、Episode 范围占位 Entity，以及上游已选完整 target 的精确绑定。具体范围见 [L2 持久化](08-memory-store.md)。本节其余规则保留为后续实体解析设计，不能据此认定首版已经实现。

L2 第一次调用输出 `subject`、`relations[].target` 和候选 `entity_mentions` 等表面文本。第二次调用对完整 target 是否值得建立身份拥有最终判定权。组件把选中的 target 合并进最终 `entity_mentions`，仍不输出 Entity ID。MemoryStore 把完整 target mention 写成 `target_entity_id`，其他 mention 写成 `entity_refs`。

写入层使用以下内容查找可能的 Entity：

- 来源明确提供的身份或角色。
- 当前 Episode。
- 已有 Entity 的 `name` 和 `aliases`。
- 已有关系型 MemoryItem。

写入层只在依据能够确定唯一身份时绑定已有 Entity。来源能够确认一个新的独立身份，但库中还没有对应 Entity 时，写入层创建 Entity，并让 `canonical_entity_id` 等于自己的 `id`。

来源能够定位当次具体对象，但不能确认长期身份时，写入层创建 `canonical_entity_id=null` 的占位 Entity。只有来源能够确认多条记录指向同一个当次对象时，才复用同一个占位 Entity。不同 Session 中都出现“副驾”不能证明是同一个人。

如果连具体对象都无法定位，写入层不创建 Entity。这样的文本可以保留在 `statement` 或 `condition` 中，但不能充当 `subject_entity_id` 或具体 `entity_refs`。

写入结果按以下方式引用 Entity：

- `subject_entity_id` 保存主张的主要主体。
- 每个实体型 relation target 保存 `{entity_id}`。
- `entity_refs` 绑定 `statement` 或 `condition` 中除完整主体和完整 relation targets 外的其他具体实体。复合 target 内部具有独立身份的实体也可以绑定。

## 延迟确认和合并

巩固可以重新读取占位 Entity 关联的 MemoryItem、Evidence 和 Raw Event。确认两个 Entity 是同一身份后，巩固把占位 Entity 的 `canonical_entity_id` 指向最终 Entity。

当前重复行为巩固实现一个明确的功能对象映射：“主驾座椅通风”对应
`seat.row1.left.ventilation_level`。多个尚未确认的 Episode 占位引用发生冲突时，
巩固核对这些实体在库中的全部引用及 Direct Evidence：每条依据必须定位到一次
驾驶员 `seat.setVentilation` 动作，原文明确提及该功能，trigger payload 明确包含
该字段，且全部来源的 `vehicle_ref` 相同。车辆功能位置构成身份，不推断硬件序列身份。
巩固选择排序后的第一个已有 ID 作为 Canonical Entity，其余 ID 直接指向它。
选择代表 ID 发生在同一身份已经证明之后。

该确认与 candidate、Support 和索引一起提交；向量化前只生成确认计划，不修改库。
写入失败则全部回滚。当前不合并已有确认身份，也不处理其他对象或无依据的同名冲突。

合并不改写 MemoryItem 中已有的 Entity 引用，Evidence 保持不变。检索时统一解析到最终 Entity，并联合读取相关记忆。

命中已合并 Entity 时，新 MemoryItem 应引用原 Entity 还是最终 Entity，由巩固负责判断。最终 Entity 再次合并时，也由巩固负责维持相关 Entity 指向新的最终 Entity。本文不规定具体方法。

错误合并可以直接拆回。身份仍不确定时把 `canonical_entity_id` 改回 `null`；已经确认独立时改为自己的 `id`。

## 关系和 Group

父子、朋友、同事和 Group 成员关系使用 MemoryItem 表达：

```text
subject_entity_id: entity-01
relations:
  - predicate: "是...的父亲"
    target: {entity_id: entity-02}
statement: "张三是王小明的父亲"
```

每个能够定位的具体孩子和朋友都是独立 Entity。一次同行或共同出现不能创建 Group。只有来源明确表示一个稳定集体时，才创建 Group Entity。Group 成员关系仍使用 MemoryItem 表达。

## 检索

检索使用名称和别名查找可能的 Entity ID。请求已经携带明确 Entity ID 时，直接使用该 ID。没有身份依据且同一名称匹配多个 Entity 时，不能合并这些 Entity，也不能在排序前指定其中一个。它们产生的 Retrieval Result 统一排序，最终返回数量仍受请求 `limit` 限制。

所有 Entity ID 在过滤和邻接扩展前都通过 `canonical_entity_id` 归一。一个 MemoryItem 即使仍引用已经合并的占位 Entity，也能被最终 Entity 的查询找到。

`subject_entity_id` 和任一实体型 relation target 可以表达有方向的实体关系。`entity_refs` 可以用于邻接候选扩展，但只表示主张提到了该实体，不能单独证明有向关系。

## 样例

已有三个 Entity：

```text
entity-01: name="张三", aliases=[], types=[Person], canonical_entity_id=entity-01
entity-02: name="王小明", aliases=["小明", "明明"], types=[Person], canonical_entity_id=entity-02
entity-03: name="李小明", aliases=["小明"], types=[Person], canonical_entity_id=entity-03
```

`entity-02` 的“明明”只有在来源已经明确它与王小明是同一身份后，才由写入层加入 `aliases`。单独出现一次“明明”不能产生这条别名。

MemoryItem 已保存“张三是王小明的父亲”。新记录只说“小明喜欢周杰伦”，没有其他身份依据。写入层不能选择 `entity-02` 或 `entity-03`，因此创建占位 `entity-04`，并让新记忆引用它：

```text
entity-04: name="这次记录中的小明", aliases=[], types=[Person], canonical_entity_id=null
```

后来直接依据表明这次说的是“张三的儿子小明”。名称和父子关系共同定位 `entity-02`，于是写入：

```text
entity-04.canonical_entity_id = entity-02
```

原 MemoryItem 仍引用 `entity-04`。检索 `entity-02` 时联合读取它。`entity-03` 不受影响。

## 不保存的结构

当前不保存 Mention、别名来源、别名置信度、参与者列表、合并历史或单独的实体关系字段。
