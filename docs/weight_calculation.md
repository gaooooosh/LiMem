# LiMem 权重计算设计文档

## 概述

LiMem 系统中的权重计算是搜索和检索的核心机制，用于评估事件与查询的相关性强度。权重综合考虑了**实体匹配精确度**、**验证频率**和**时间衰减**三个维度。

## 核心公式

### 完整权重公式

```
w_ij = log(1 + c_valid) × exp(-λ × log(1 + t_now - t_valid)) × entity_match_factor
```

其中：
- `w_ij`: 事件 i 通过实体 j 的边权重
- `c_valid`: 关系的验证次数
- `λ`: 时间衰减率（`SEARCH_LAMBDA`）
- `t_now`: 当前时间戳
- `t_valid`: 关系最后验证时间戳
- `entity_match_factor`: 实体匹配精确度因子

## 组件详解

### 1. 实体匹配权重 (entity_match_factor)

实体匹配因子反映查询实体与数据库实体的匹配精确度，通过**混合匹配策略**计算：

#### 1.1 匹配类型

| 匹配类型 | 说明 | 权重 |
|---------|------|------|
| 精确匹配 (Exact) | 字符串完全一致 | 1.0 |
| 向量匹配 (Vector) | 基于语义相似度 | 相似度分数 [0.5, 1.0] |
| 混合匹配 (Both) | 同时满足上述两种 | 1.0（精确匹配优先） |

#### 1.2 计算方法

```python
# 伪代码
entity_match_factor = average(all_entity_match_weights)
```

对于每个事件，计算其所有相关实体匹配权重的平均值：

```python
# 示例 1: 事件连接 2 个精确匹配实体
factor = (1.0 + 1.0) / 2 = 1.0

# 示例 2: 事件连接 1 个精确匹配 + 1 个相似度 0.8 的实体
factor = (1.0 + 0.8) / 2 = 0.9

# 示例 3: 事件连接 2 个相似度 0.7 的实体
factor = (0.7 + 0.7) / 2 = 0.7
```

#### 1.3 向量相似度匹配

当启用向量匹配时（`SEARCH_ENABLE_VECTOR_MATCH=true`），系统会：

1. 为查询实体生成嵌入向量
2. 计算与数据库中所有实体嵌入的余弦相似度
3. 保留相似度 ≥ 阈值（`SEARCH_VECTOR_THRESHOLD`）的实体
4. 返回 Top-K（`SEARCH_VECTOR_TOP_K`）最相似的实体

**余弦相似度公式**：
```
similarity = dot(A, B) / (||A|| × ||B||)
```

### 2. 频率强化 (Frequency Reinforcement)

```
frequency_factor = log(1 + c_valid)
```

- `c_valid`: 关系被验证的次数
- 使用对数函数避免线性爆炸增长
- 每次验证增加 `c_valid`，权重持续累积

**增长曲线**：
```
c_valid=0  → factor = 0
c_valid=1  → factor = 0.693
c_valid=10 → factor = 2.398
c_valid=100 → factor = 4.615
```

### 3. 时间衰减 (Temporal Decay)

```
decay_factor = exp(-λ × (t_now - t_valid))
```

- `λ`: 衰减率（`SEARCH_LAMBDA`），默认 `1e-9`
- `Δt = t_now - t_valid`: 距离上次验证的时间差
- 指数衰减确保旧记忆逐渐淡化

**衰减特性**：
```
Δt = 0      → factor = 1.0      (无衰减)
Δt = 1秒    → factor ≈ 1.0     (几乎无衰减)
Δt = 1天    → factor ≈ 0.92    (轻微衰减)
Δt = 1年    → factor ≈ 0.03    (显著衰减)
```

### 4. 硬过滤条件 (Hard Filters)

在计算权重前，会先进行硬过滤，以下情况权重直接为 **0**：

```python
# 1. 过期事件
if t_expired is not None:
    return 0.0

# 2. 失效事件
if t_invalid is not None and t_now >= t_invalid:
    return 0.0
```

## 配置参数

### 搜索权重相关参数

| 参数 | 环境变量 | 默认值 | 说明 |
|------|---------|--------|------|
| `SEARCH_LAMBDA` | `SEARCH_LAMBDA` | `1e-9` | 时间衰减率，控制记忆淡化的速度 |
| `SEARCH_TOP_K` | `SEARCH_TOP_K` | `5` | 返回的最相关事件数量 |
| `SEARCH_ENABLE_VECTOR_MATCH` | `SEARCH_ENABLE_VECTOR_MATCH` | `true` | 是否启用向量语义匹配 |
| `SEARCH_VECTOR_THRESHOLD` | `SEARCH_VECTOR_THRESHOLD` | `0.5` | 向量匹配的最小相似度阈值 |
| `SEARCH_VECTOR_TOP_K` | `SEARCH_VECTOR_TOP_K` | `10` | 向量匹配返回的最大实体数 |

### 事件优先级与衰减率

在 `ltm.py` 中，事件优先级决定存储时的衰减率：

| 优先级 | 说明 | 衰减率参数 | 默认值 |
|--------|------|-----------|--------|
| P1 | 永久特质 | `DECAY_RATE` (固定) | 0.0 (不衰减) |
| P2 | 半永久偏好 | `DECAY_RATE_P2` | 0.01 |
| P3 | 短暂事件 | `DECAY_RATE_P3` | 0.02 |

**注意**：搜索时使用统一的 `SEARCH_LAMBDA`，存储时使用基于优先级的衰减率。

## 检索流程

### 四阶段检索管道

```
┌─────────────────┐
│ 1. 实体提取     │  LLM 从查询中提取实体
└───────┬────────┘
        ↓
┌─────────────────┐
│ 2. 图路径搜索    │  混合匹配（精确+向量）+ 边属性查询
└───────┬────────┘
        ↓
┌─────────────────┐
│ 3. 权重重排序   │  计算权重并排序
└───────┬────────┘
        ↓
┌─────────────────┐
│ 4. LLM 摘要    │  生成上下文相关答案
└─────────────────┘
```

### 数据结构

#### 事件-实体关系 (INVOLVES 边)

```cypher
(:Event)-[r:INVOLVES]->(:Entity)
```

**边属性**：
- `t_created`: 关系创建时间
- `t_valid`: 最后验证时间
- `t_expired`: 过期时间（可为空）
- `t_invalid`: 失效时间（可为空）
- `c_valid`: 验证次数

#### 检索返回的事件数据

```python
{
    "event_id": str,
    "summary": str,
    "priority": str,
    "c_valid": int,
    "t_valid": int,
    "t_expired": int | None,
    "t_invalid": int | None,
    "entity_match_weights": {
        "entity_id_1": 1.0,      # 精确匹配
        "entity_id_2": 0.85,     # 向量匹配
    },
    "match_type": "exact" | "vector" | "both"
}
```

## 使用示例

### Python API

```python
from limem.search import LTMSearcher, RetrievalConfig

# 初始化 searcher
searcher = LTMSearcher(conn)

# 自定义配置
config = RetrievalConfig(
    lambda_param=1e-9,           # 时间衰减率
    enable_vector_match=True,      # 启用向量匹配
    vector_similarity_threshold=0.6,  # 向量匹配阈值
    default_top_k=5               # 返回 Top-5
)
searcher = LTMSearcher(conn, config)

# 执行搜索
result = searcher.search("用户喜欢什么音乐？", top_k=3)

# 输出结果
print(f"提取的实体: {result['entities']}")
for i, event in enumerate(result['top_k_events'], 1):
    print(f"{i}. {event.summary}")
    print(f"   权重: {event.weight:.4f}")
    print(f"   验证次数: {event.c_valid}")
```

### 权重计算示例

假设有以下场景：

**事件 A**: "用户喜欢听爵士乐"
- `c_valid = 10`
- `t_valid = 1704067200` (2024-01-01)
- `t_now = 1735689600` (2025-01-01)
- 连接实体：["用户"(精确1.0), "爵士乐"(精确1.0)]

**事件 B**: "用户听过流行音乐"
- `c_valid = 5`
- `t_valid = 1704067200` (2024-01-01)
- `t_now = 1735689600` (2025-01-01)
- 连接实体：["用户"(精确1.0), "流行音乐"(向量0.75)]

**查询**: "用户喜欢听什么音乐？"
- 提取实体：["用户", "音乐"]

**计算**：

事件 A:
```
frequency_factor = log(1 + 10) = 2.398
decay_factor = exp(-1e-9 × 31536000) = 0.969
entity_match_factor = (1.0 + 1.0) / 2 = 1.0
weight = 2.398 × 0.969 × 1.0 = 2.324
```

事件 B:
```
frequency_factor = log(1 + 5) = 1.792
decay_factor = exp(-1e-9 × 31536000) = 0.969
entity_match_factor = (1.0 + 0.75) / 2 = 0.875
weight = 1.792 × 0.969 × 0.875 = 1.521
```

**结果**：事件 A 排名更高，因为：
1. 更高的验证次数（10 vs 5）
2. 更高的实体匹配精确度（全精确匹配 vs 部分向量匹配）

## 实现文件

- **核心实现**: `src/limem/search.py`
  - `LTMSearcher._calculate_weight()` (search.py:387-436)
  - `LTMSearcher.fetch_weighted_events()` (search.py:262-381)
  - `LTMSearcher._vector_match_entities()` (search.py:198-260)

- **配置**: `src/limem/config.py`
  - `SEARCH_LAMBDA`, `SEARCH_TOP_K`, `SEARCH_ENABLE_VECTOR_MATCH` 等

- **数据模型**: `src/limem/models.py`
  - `RankedEvent` (models.py:202-227)
  - `Priority` 枚举 (models.py:13-23)

## 设计原理

### 为什么使用对数频率强化？

1. **避免线性爆炸**: 线性增长会让高频事件权重无限膨胀
2. **边际递减**: 每次新增验证的收益递减，符合认知规律
3. **区分度保持**: 即使验证次数差异很大，权重差异仍可控

### 为什么使用指数时间衰减？

1. **生物学合理性**: 记忆遗忘曲线符合指数规律 (Ebbinghaus)
2. **持续淡化**: 长时间未激活的记忆逐渐被遗忘
3. **可调性**: 通过 λ 参数可灵活控制遗忘速度

### 为什么使用平均实体匹配因子？

1. **多实体平衡**: 事件通常涉及多个实体，平均值能综合反映
2. **精确度敏感**: 匹配越精确，因子越接近 1.0
3. **模糊匹配惩罚**: 语义相似但非精确的匹配会降低因子

## 调优建议

### 调整时间衰减率

```bash
# 快速遗忘（适用于短期偏好）
export SEARCH_LAMBDA=1e-8

# 慢速遗忘（适用于长期习惯）
export SEARCH_LAMBDA=1e-10
```

### 调整向量匹配阈值

```bash
# 更严格的语义匹配
export SEARCH_VECTOR_THRESHOLD=0.7

# 更宽松的语义匹配
export SEARCH_VECTOR_THRESHOLD=0.4
```

### 调整 Top-K 返回数量

```bash
# 返回更多结果（适用于推荐场景）
export SEARCH_TOP_K=10

# 返回更少结果（适用于精确问答）
export SEARCH_TOP_K=3
```

## 未来扩展

### 可能的改进方向

1. **个性化衰减率**: 根据用户行为模式动态调整 λ
2. **上下文感知**: 根据对话上下文调整实体匹配权重
3. **多路径聚合**: 考虑多个实体-实体路径的权重叠加
4. **优先级感知**: 搜索时也考虑事件优先级（P1/P2/P3）
5. **时序模式**: 考虑事件的时间序列模式

## 参考链接

- 主实现: `src/limem/search.py`
- 配置文件: `src/limem/config.py`
- 数据模型: `src/limem/models.py`
- 长时记忆系统: `src/limem/ltm.py`
