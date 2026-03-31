# LiMem Core

`src/limem/` 是 LiMem 的核心算法库。

它负责把原始输入组织为长期可检索、可演化的记忆图；它不应该包含特定数据集、特定前端页面、特定脚本工作流或某个业务场景专用的调试逻辑。领域特定能力应该放在 `src/script/`，或通过配置注入到核心算法中。

## 设计目标

- 抽象统一的长期记忆接口，而不是耦合某个数据集格式
- 在线写入时保持增量更新，避免全图重建
- 将“原始输入”“结构化事件”“稳定上下文”“图检索”拆成独立层
- 为不同场景保留扩展点：提取器、存储层、上下文规则、实体归一化规则
- 默认提供可直接运行的主路径：`create_ltm()`

## 边界

`limem` 负责：

- 核心数据模型
- Episode -> Event 的构建
- Entity / Context / Event-Event 关系维护
- 记忆检索与排序
- 动态演化、压缩、归并、归档
- 图存储抽象与 Kuzu 实现

`limem` 不负责：

- 特定数据集加载逻辑
- trips 调试页面和调试脚本
- 面向某个业务场景硬编码的一次性流程

## 核心抽象

### 1. Episode

原始输入单元，保存原文和时间戳，用于溯源与重放。

### 2. Event

从 `Episode` 提取出的最小动态变化单元，是当前主路径中的在线写入核心对象。

关键字段：

- `summary`: 检索和压缩时最重要的语义摘要
- `action`: 事件动作
- `causality`: 原因、结果或补充因果信息
- `participants`: 参与者列表
- `time_range`: 结构化时间信息
- `evidence`: 证据片段
- `status`: `active / merged / archived / ignored`

### 3. Entity

用于命名对象索引和显式符号匹配。它是候选召回入口之一，但不是语义主干本身。

### 4. Context

表示可复用的稳定条件，如场景、状态、约束、目标、环境、阶段。`Context` 通过 `ContextExtractionPipeline` 从事件文本和事件负载中抽取，再由动态引擎做复用、冲突处理、合并和衰减。

### 5. LTMemory

核心对外接口。默认实现是 `LTMemoryImpl`。

常用方法：

- `ingest(episode)`
- `search(query, top_k=5, generate_answer=True)`
- `retrieve_memories(query, top_k=5)`
- `run_consolidation(dry_run=False, strategy="auto")`
- `write(...) / remove(...) / merge_event(...) / merge_context(...)`
- `get_stats()`

## 分层结构

```text
src/limem/
├── core/            # 核心数据模型与抽象接口
├── builder/         # 写入构建链路：提取、归一化、构建、上下文抽取
├── retriever/       # 查询链路：实体匹配、召回、排序、回答生成
├── evolution/       # 增量演化、局部更新、融合、归档
├── storage/         # 图存储抽象与 Kuzu 实现
├── factory.py       # 系统装配入口
├── ltmemory_impl.py # LTMemory 统一实现
├── ops.py           # 面向调试/前端的图操作接口
├── migration.py     # 兼容迁移工具
└── utils.py         # 通用归一化与辅助函数
```

## 在线写入链路

默认主路径是 append-first：

1. 保存原始 `Episode`
2. 通过 `TwoStageExtractor` 抽取一个或多个事件与实体
3. 规范化事件字段
4. 将 `Event` 直接追加写入图中
5. 建立 `Event -> Episode`、`Event -> Entity` 关系
6. 由 `DynamicEvolutionEngine` 局部解析并附着 `Context`
7. 可选触发局部事件关系和定期 consolidation

这个设计的目标是：

- 在线路径简单稳定
- 避免边写入边做高成本全局合并
- 把“压缩”和“整理”放到后续 bounded consolidation

## 检索链路

`MemorySearcher` 的主路径分为四步：

1. 从查询中抽取实体
2. 通过 `EntityMatcher` 做精确/模糊匹配
3. 结合实体索引路和上下文语义路召回候选事件
4. 由 `MemoryRanker` 综合相似度、时间、支持度等信号排序

如果启用动态演化，还可以通过 `retrieve_memories()` 获取更适合小模型消费的压缩检索结果。

## 动态演化

`DynamicEvolutionEngine` 是当前版本里最重要的增量图算法层，负责：

- 事件写入后的局部上下文解析
- Context 复用与冲突分叉
- Event / Context 的自动融合
- 归档、衰减、清理
- 面向检索的压缩输出

当前核心库中的语义主干是：

```text
Episode -> Event
Event -> Entity
Event -> Context
Event -> Event
```

`Entity` 仍然保留为索引层；`Context` 和 `Event-Event` 关系承担更主要的长期语义组织职责。

## 快速开始

### 创建系统

```python
from limem import create_ltm

ltm = create_ltm(
    db_path="./DB/memory.kz",
    config={
        "offline_mode": True,
        "enable_dynamic_evolution": True,
        "append_first_mode": True,
        "generate_answer": False,
    },
)
```

### 写入一条记忆

```python
from limem import Episode

episode = Episode(
    content="用户说：导航去公司，车机回答：已开始导航。",
    timestamp=1710000000,
)

result = ltm.ingest(episode)
print(result.to_dict())
```

### 查询记忆

```python
search_result = ltm.search("用户和导航相关的行为是什么？", top_k=5, generate_answer=False)

for item in search_result.top_k_events:
    print(item.event_id, item.weight, item.summary)
```

### 获取演化感知检索结果

```python
rows = ltm.retrieve_memories("用户在会议场景下会怎么设置车机？", top_k=5)
for row in rows:
    print(row["event_id"], row.get("summary"))
```

### 运行融合

```python
report = ltm.run_consolidation(dry_run=True, strategy="auto")
print(report)
```

## 关键入口

### `factory.py`

推荐从这里启动系统：

- `create_ltm()`
- `create_ltm_from_env()`

它负责组装：

- `KuzuStore`
- `TwoStageExtractor`
- `MemoryBuilder`
- `MemorySearcher`
- `DynamicEvolutionEngine`

### `ltmemory_impl.py`

统一暴露核心 API，是业务侧最常直接依赖的实现。

### `ops.py`

提供更稳定的图操作接口，适合调试面板、脚本或人工修正流：

- `write`
- `remove`
- `query`
- `snapshot`
- `auto_merge`
- `merge_event`
- `merge_context`

## 可配置与可扩展点

### 1. 存储层

通过 `GraphStore` 抽象存储接口，当前默认实现是 `KuzuStore`。如果未来需要 Neo4j、SQLite Graph 或内存版 store，应优先对齐这个接口，而不是在业务层绕过它。

### 2. 提取层

`MemoryBuilder` 依赖 `LLMExtractor` 抽象，默认实现是 `TwoStageExtractor`。如果要切换模型或抽取策略，应替换提取器，而不是改 `LTMemoryImpl`。

### 3. 上下文抽取规则

`ContextExtractionPipeline` 支持 `domain_config`，可以覆盖：

- habit-like markers
- event-like markers
- strong context signal markers
- generic clause patterns
- low-value literal markers
- minimum-context 推断规则
- context phrase 抽象映射

这类覆盖适合做领域迁移，而不是把词表继续硬编码回核心逻辑。

### 4. 事件/实体归一化规则

`utils.py` 中的 `normalize_event_payload()`、`normalize_entity_candidates()` 以及相关 helper 已支持默认规则加可选覆盖参数。核心库保留默认值以保证兼容，但新的业务域应该通过参数注入，不应继续把场景词表写死在函数内部。

## 与 `src/script/` 的关系

建议把系统分成两层理解：

- `src/limem/`: 可复用核心库
- `src/script/`: 数据集脚本、调试入口、演示程序、批处理工具

判断原则很简单：

- 如果代码描述的是抽象记忆算法，应放 `limem`
- 如果代码依赖某个数据集、某个 HTML 调试页、某个一次性回放流程，应放 `script`

## 推荐阅读顺序

如果要快速读懂代码，建议按下面顺序：

1. `src/limem/__init__.py`
2. `src/limem/factory.py`
3. `src/limem/ltmemory_impl.py`
4. `src/limem/builder/memory_builder.py`
5. `src/limem/retriever/memory_searcher.py`
6. `src/limem/evolution/dynamic_engine.py`
7. `src/limem/storage/graph_store.py`
8. `src/limem/storage/kuzu_store.py`

## 相关文档

- `docs/system_architecture.md`
- `docs/dynamic_evolution_graph.md`
- `docs/算法说明文档.md`
- `docs/memory_structure_design.md`

## 当前约束

截至 2026-03-31，`limem` 仍然有一些持续演进中的部分：

- 部分旧文档仍然描述早期结构，和当前 append-first 主路径不完全一致
- 一些脚本仍面向 trips/车载场景，但这些逻辑应继续留在 `src/script/`
- Pattern 抽象在文档中出现过，但当前核心主路径更聚焦 `Event / Entity / Context / Event-Event`

因此，修改核心库时应优先遵守当前实际代码，而不是历史文档。
