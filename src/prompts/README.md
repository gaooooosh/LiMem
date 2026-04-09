# LiMem Prompts

本目录保存当前主路径使用的提示词模板。

## 提取相关

| 文件 | 用途 | 主要调用位置 |
|------|------|--------------|
| `extract_unified_system.txt` | 单次调用统一提取事件与内联 contexts 的系统提示 | `builder/extractor.py` |
| `extract_unified_user.txt` | 单次调用统一提取的用户模板 | `builder/extractor.py` |
| `extract_relation_system.txt` | 事件关系提取系统提示 | `evolution/dynamic_engine.py` |

## 模板变量

- `extract_unified_user.txt`: `{episode_text}`

## 加载方式

所有 prompt 通过 `src/limem/utils.py` 的 `load_prompt()` 动态读取。
