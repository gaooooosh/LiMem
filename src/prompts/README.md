# LiMem Prompts

本目录保存当前主路径使用的提示词模板。Prompt 按用途分组，代码通过
`src/limem/utils.py` 的 `load_prompt()` 动态读取，并传入相对 `src/prompts/`
的路径。

## 目录结构

```text
src/prompts/
├── extraction/   # Episode -> Event / inline Context 的主抽取
├── context/      # Event / record -> 可复用 Context 的抽取与批处理
└── evolution/    # Event 关系、更新、补充、合并、派生等演化操作
```

## Extraction

| 文件 | 用途 | 主要调用位置 |
|------|------|--------------|
| `extraction/extract_unified_system.txt` | 单次调用统一提取事件与内联 contexts 的系统提示 | `builder/extractor.py` |
| `extraction/extract_unified_user.txt` | 单次调用统一提取的用户模板 | `builder/extractor.py` |
| `extraction/extract_combined_system.txt` | 组合抽取兼容提示 | 备用/脚本 |
| `extraction/extract_combined_user.txt` | 组合抽取兼容用户模板 | 备用/脚本 |

## Context

| 文件 | 用途 | 主要调用位置 |
|------|------|--------------|
| `context/extract_context_system.txt` | 上下文抽取系统提示 | `builder/context_extractor.py` |
| `context/extract_context_user.txt` | 单事件上下文抽取用户模板 | `builder/context_extractor.py` |
| `context/extract_context_batch_user.txt` | 批量上下文抽取用户模板 | `builder/context_extractor.py` |

## Evolution

| 文件 | 用途 | 主要调用位置 |
|------|------|--------------|
| `evolution/classify_relations_system.txt` | 事件候选关系/操作分类系统提示 | `evolution/relation_processor.py` |
| `evolution/classify_relations_user.txt` | 事件候选关系/操作分类用户模板 | `evolution/relation_processor.py` |
| `evolution/extract_relation_system.txt` | 旧版事件关系提取兼容提示 | `evolution/dynamic_engine.py` |
| `evolution/fuse_event_system.txt` | update/extend 版本事件融合系统提示 | `evolution/relation_processor.py` |
| `evolution/fuse_event_user.txt` | update/extend 版本事件融合用户模板 | `evolution/relation_processor.py` |
| `evolution/derive_event_system.txt` | 派生事件系统提示 | `evolution/relation_processor.py` |
| `evolution/derive_event_user.txt` | 派生事件用户模板 | `evolution/relation_processor.py` |
| `evolution/rewrite_merged_event_system.txt` | 合并事件语义重写系统提示 | `evolution/relation_processor.py`, `evolution/dynamic_engine.py` |
| `evolution/rewrite_merged_event_user.txt` | 合并事件语义重写用户模板 | `evolution/relation_processor.py`, `evolution/dynamic_engine.py` |

## 模板变量

- `extraction/extract_unified_user.txt`: `{episode_text}`
- `context/extract_context_user.txt`: `{record_text}`, `{event_json}`, `{existing_contexts_section}`
- `context/extract_context_batch_user.txt`: `{payload_json}`
- `evolution/classify_relations_user.txt`: `{payload_json}`
- `evolution/fuse_event_user.txt`: `{payload_json}`
- `evolution/derive_event_user.txt`: `{payload_json}`
- `evolution/rewrite_merged_event_user.txt`: `{payload_json}`
