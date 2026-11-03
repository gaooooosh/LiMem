# Workflow1 v4

正式入口：

```python
from pathlib import Path
from limem.llm_client import QwenLLMClient
from limem.memory_item_extractor.factory import create_workflow1_extractor

extractor = create_workflow1_extractor(QwenLLMClient(), trace_root=Path('var/debug/workflow1-v4'))
result = extractor.extract(episode, claim_episode=claim_episode,
                           user_ref=raw_session['user_id'], raw_session=raw_session)
```

HTTP 写入和独立评测均使用此工厂，固定执行 v4。`episode` 提供完整证据背景，`claim_episode` 提供主张抽取视图，`raw_session` 提供 BERT 与实体身份边界检查所需的直接来源字段。

## 处理顺序

- G：以 required_actions 决定固定主体/主来源或自由生成。其余语义由 Qwen 从原文抽取。
- 校验主体与来源覆盖；T 只从当前初稿补写空 target。
- C：逐个判断 target 为 entity、parameter、other。只有 entity 进入实体提及；关系原文与参数仍保留。
- 程序合并提及，并从完整 Episode 按引用拼接 context。
- N：逐项筛选其他 entity_mentions。
- B：无 required_actions 时，以原始文字运行 CPU BERT；Session 最大分数低于 0.5 则清空整份 L2。
- I：按协议字段、枚举和来源名称筛选没有身份含义的提及；再次复核必保覆盖。

不运行旧无动作检查模型，不进行媒体/车窗受限论元归位。调用、结构、来源或覆盖校验失败均保留错误并结束；没有自动重试或回退。

`extractor.py` 中的 `MemoryItemExtractor` 是主张校验与 L2 组装组件。v4 工厂向它注入主张生成、target 重构和目标类型处理器；正式运行应使用工厂。

## 输出

```text
L2Result {
  items: [{
    subject: text,
    relations: [{predicate: text, target: text | null}],
    statement: text,
    condition: text | null,
    context: text,
    entity_mentions: text[],
    raw_event_ids: text[]
  }]
}
```

- subject：主张主体文字。必保动作的主体由程序固定为来源用户。
- relations：有序的关系或动作；target 保存对象、结果或参数文字，非空不代表一定是实体。
- statement：该条记忆的完整语义；condition 为原文直接支持的条件，两者只允许单行。
- context：程序从完整 Episode 中按最终引用拼接的证据背景。
- entity_mentions：需要后续建立身份的临时提及，不是持久实体 ID。
- raw_event_ids：真实来源 ID，必须属于当前 Episode，满足去重、顺序和必保覆盖要求。

同一条 item 的关系共享主体、条件、Evidence 和生命周期。模型不输出 Entity ID；后续 MemoryStore 完成实体绑定与持久化。

部署与流程见 `docs/workflow1/README.md`。主张提示保存在本目录的claim_prompt.json；不在此分支附带研发评测数据和实验报告。
