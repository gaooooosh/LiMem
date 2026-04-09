# LiMem Prompts

本目录保存当前主路径使用的提示词模板。

## 提取相关

| 文件 | 用途 | 主要调用位置 |
|------|------|--------------|
| `extract_event_system.txt` | 提取事件的系统提示 | `builder/extractor.py` |
| `extract_event_user.txt` | 提取事件的用户模板 | `builder/extractor.py` |
| `extract_event_only_system.txt` | 仅提取事件的系统提示 | `builder/extractor.py` |
| `extract_event_only_user.txt` | 仅提取事件的用户模板 | `builder/extractor.py` |
| `extract_event_segments_system.txt` | 事件切分（Stage A）系统提示 | `builder/extractor.py` |
| `extract_event_segments_user.txt` | 事件切分（Stage A）用户模板 | `builder/extractor.py` |
| `extract_event_struct_system.txt` | 单片段事件结构化（Stage B）系统提示 | `builder/extractor.py` |
| `extract_event_struct_user.txt` | 单片段事件结构化（Stage B）用户模板 | `builder/extractor.py` |
| `extract_entities_only_system.txt` | 仅提取实体的系统提示 | `builder/extractor.py` |
| `extract_entities_only_user.txt` | 仅提取实体的用户模板 | `builder/extractor.py` |
| `extract_relation_system.txt` | 事件关系提取系统提示 | `evolution/dynamic_engine.py` |

## 模板变量

- `extract_event_user.txt`: `{episode_text}`
- `extract_event_segments_user.txt`: `{episode_text}`
- `extract_event_struct_user.txt`: `{episode_text}`, `{segment_text}`, `{segment_index}`, `{segment_total}`

## 加载方式

所有 prompt 通过 `src/limem/utils.py` 的 `load_prompt()` 动态读取。
