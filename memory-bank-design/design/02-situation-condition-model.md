# 情境与条件

## 目标

MemoryItem 需要表达可以被当前情境触发的 IF-THEN 记忆，例如：

```text
IF 下雨且正在驾驶
THEN 用户A更倾向于听萧敬腾的歌
```

L2 使用两个自然语言字段保存它：

```text
statement: "用户A更倾向于听萧敬腾的歌"
condition: "下雨且正在驾驶时"
```

`statement + condition` 共同构成完整主张。两者不能互相矛盾。

## Condition 的边界

`condition` 表示主张成立或适用的条件。它不是一次发生时全部环境的摘要。

下面是一次直接行为：

```text
statement: "用户A在下雨且驾驶过程中请求播放萧敬腾"
condition: null
```

它只说明当时发生了什么，不能直接推出以后遇到相同情境还会这样做。

下面是条件规律：

```text
statement: "用户A更倾向于听萧敬腾的歌"
condition: "下雨且正在驾驶时"
```

只有两种来源可以产生第二种结构：

1. 用户或其他来源明确陈述 IF-THEN 规律，L2 直接抽取。
2. 巩固根据多次独立 Evidence 归纳出规律。

## 表达形式

Condition 保存完整自然语言，不拆成 dimension、value、operator 或 unit。

```text
上午
10:00以后
周末或节假日时
车内炎热时
车内温度超过36°C时
小明和小红都在车上时
用户A的任意一个孩子在车上时
```

存储层不声明以下表达等价：

- `上午` 与 `10:00`
- `炎热` 与 `36°C`
- `孩子在车上` 与某几个已知孩子在车上

这些关系需要结合实际使用方和评测判断，不能提前固化在 Schema 中。

否定、AND、OR 和数量也直接写入自然语言：

```text
statement: "用户A不喜欢听爵士"
condition: "开车时"
```

```text
statement: "用户A更愿意外出"
condition: "周末或节假日时"
```

不为这些表达增加逻辑表达式字段。

## 条件中的实体

具体实体通过 `entity_refs` 固定身份：

```text
condition: "小明和小红都在车上时"
entity_refs: [
  {text: "小明", entity_id: "entity-02"},
  {text: "小红", entity_id: "entity-03"}
]
```

同一组引用可以出现在“都在”“任意一个在”或“只有小明在”等不同条件中。条件含义由文本决定，不能从引用数组推导。

通用角色不绑定具体实体：

```text
condition: "用户A的任意一个孩子在车上时"
entity_refs: []
```

如果巩固把“小明在车上时”泛化成“用户A的任意一个孩子在车上时”，新候选不保留小明的精确引用，但不能丢失孩子与用户A的关系范围。

## 条件规律的来源支撑

一次直接行为的 Evidence 必须覆盖完整的 IF 和 THEN 依据。

例如一次“雨天驾驶时请求播放萧敬腾”由三条 Raw Event 共同支持：

```text
raw_event_ids: [weather-rain, activity-driving, request-xiao-jingteng]
```

第二次独立发生形成另一条直接 Evidence。不能把整个 Episode 当作一次支撑，也不能
只保存请求记录后声称天气和活动已经得到证明。巩固产生的 candidate 不新建
Evidence。它通过 ConsolidationSupport 连接这些独立 Evidence，并只保存实际使用的
原文片段。

用户在一条 Raw Event 中明确说出完整条件偏好时，一条 Raw Event 可以构成完整 Evidence。

## L2 与巩固

L2 负责保存直接陈述和直接行为。它不跨 Episode 判断规律。

巩固可以回读相关 Evidence、Episode 和 Raw Event，比较多次行为发生时的情境，并产生 `status=candidate` 的条件规律。

巩固不能把已有候选规律当作新的原始行为证据。每条新候选都必须回到 Raw Event。

## 验证要求

条件结构至少需要通过以下 case：

1. 用户明确说出条件偏好时，L2 直接写 active MemoryItem。
2. 单次行为只形成行为主张，不形成条件偏好。
3. 多次行为形成多个独立 Evidence，巩固通过 ConsolidationSupport 提出 candidate。
4. `上午`、`10:00`、`炎热`、`36°C` 能原样保存，不被错误归一。
5. AND、OR、否定和数量条件能够完整保存。
6. 具体人物条件能够绑定实体；通用角色不会被错误缩窄。

自然语言 condition 的召回率、匹配准确率和延迟尚未验证。检索设计需要用上述固定 case 评测，不反向要求 L2 增加结构化条件字段。
