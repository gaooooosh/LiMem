# Resolver

## 目标

`Resolver[InputT, OutputT]` 只统一强类型解析接口：

```text
resolution_description
resolve(input) -> output
```

父类不持有 LLMClient、MemoryStore、重试或 fallback。具体子类自行实现解析策略。

`QueryResolver` 是首个子类。它把自然语言 `query/context` 中有明确文本依据的表面词链接到当前已有 Entity。首版不调用 LLM，不处理代词或共指。

## 接口

```python
from limem.resolver import QueryResolutionInput, QueryResolver

resolver = QueryResolver(memory_store)
resolved = resolver.resolve(
    QueryResolutionInput(
        query="播放小明喜欢的歌",
        context="当前副驾是王小明",
    )
)
```

输入中的 `query` 和 `context` 可以有一个为 `null`，不能同时为 `null` 或空白文本。输出原样保留两段文本：

```text
ResolvedQuery {
  query
  context
  entity_mentions: [
    {
      text
      source: query | context
      entity_ids: EntityId[]
    }
  ]
}
```

## MemoryStore 读取接口

`QueryResolver` 每次调用 `resolve()` 时读取一次 `MemoryStore.list_entities()`。每个 Entity 读取以下字段：

```text
id
name
aliases
scope_text
```

`scope_text` 让自然语言可以命中展示名称带 Episode 前缀的占位 Entity。Resolver 不读取数据库连接，不缓存 Entity，也不修改 MemoryStore。

## 匹配规则

- 精确匹配 Entity 的 `name`、每个 `alias` 和非空 `scope_text`。
- mention 按 query 在前、context 在后，再按文本位置排列。
- 重叠候选只保留最长表面词。
- 同一表面词属于多个 Entity 时，在同一 mention 的 `entity_ids` 中保留全部 ID。
- 没有匹配的名称和代词不生成 Entity ID，原始文本仍保留在输出中。
- 每次解析使用 MemoryStore 的当前 Entity 数据，不重试、不回退、不改写文本。

## 契约 Case

`data/test/resolver/contract-cases.json` 保存人工标注的小 case，覆盖显式名称、
alias、`scope_text`、最长匹配、同名多 ID、context 中的实体、歧义代词、
关系描述和未知实体。case 只验证 QueryResolver 的链接职责，不把检索排序或
上游 Entity 漏抽归入 Resolver。
