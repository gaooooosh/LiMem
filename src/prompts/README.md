# LiMem Prompts 目录

本目录存储LiMem系统中使用的所有LLM提示词(Prompts)。

## 文件说明

### 记忆保存相关 (LTM)

| 文件 | 用途 | 调用位置 |
|------|------|----------|
| `extract_event_system.txt` | 从原始对话中提取事件和实体的系统提示 | `ltm.py:extract_event_from_llm()` |
| `extract_event_user.txt` | 从原始对话中提取事件和实体的用户提示模板 | `ltm.py:extract_event_from_llm()` |

### 搜索检索相关 (SEARCH)

| 文件 | 用途 | 调用位置 |
|------|------|----------|
| `entity_extraction_system.txt` | 从查询中提取实体的系统提示 | `search.py:extract_entities()` |
| `entity_extraction_user.txt` | 从查询中提取实体的用户提示模板(包含few-shot示例) | `search.py:extract_entities()` |
| `generate_answer_system.txt` | 基于检索事件生成答案的系统提示 | `search.py:generate_answer()` |
| `generate_answer_user.txt` | 基于检索事件生成答案的用户提示模板 | `search.py:generate_answer()` |

## 模板变量

部分prompt文件使用Python `.format()` 方式进行变量替换：

### extract_event_user.txt
- `{episode_text}` - 原始对话内容

### entity_extraction_user.txt
- `{query}` - 用户查询字符串

### generate_answer_user.txt
- `{events_context}` - 格式化后的相关事件上下文
- `{query}` - 用户查询字符串

## 修改Prompt

如需修改prompt，直接编辑对应的txt文件即可，下次运行时会自动加载新版本。

## 加载方式

所有prompt通过 `src/limem/utils.py` 中的 `load_prompt()` 函数动态读取：

```python
from .utils import load_prompt

system_msg = load_prompt("extract_event_system.txt")
user_msg = load_prompt("extract_event_user.txt").format(episode_text=text)
```
