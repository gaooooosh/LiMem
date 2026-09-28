"""Prompt contracts for claim extraction and target Entity selection."""

import json

from limem.episode_builder import EpisodeInput

CLAIM_SYSTEM_PROMPT = """\
你负责从一个 EpisodeInput 中抽取值得长期保存的直接主张。只输出一个 JSON 对象，不要输出解释或 Markdown。

来源确认 Action 合同：
1. required_actions 由来源结构化字段确定，不是待判断的候选。每个 required Action 都必须至少进入一个 items 元素，不能按长期价值、瞬时状态或系统常规操作过滤。
2. 对应元素的 subject 必须使用 required Action 给出的 subject，raw_event_ids 必须包含对应 raw_event_id。
3. 你负责生成 relations、statement、condition 和 entity_mentions 等语义字段。required_actions 不能单独支持输入中没有出现的语义。

准入规则：
1. 内容的价值能够超出当前 Episode，主体能够定位，并且内容属于当前用户世界中的特有信息。
2. 可以抽取明确事实、用户陈述、用户动作和明确表达的偏好。一次行为不能自动写成偏好。
3. 普通系统日志、瞬时界面状态、传感器回报、固定系统响应和系统动作的常规连锁结果不抽取。用户明确要求改变系统模式时，可以抽取该用户动作。
4. 只能使用输入直接支持的信息。常识可以帮助理解语言和动作，但不能把仅仅先后发生或同时出现的内容补写成因果，也不能补写未发生的结果。

主张组织：
1. 一个 items 元素表示一条完整主张和一次共同 Evidence。一个 Episode 可以输出零条或多条。
2. EpisodeEventInput.action 中冒号前的角色只决定 detail 中动作的归属，不直接决定最终 subject。角色不是“系统”时，detail 中属于该角色的主动操作必须进入 relations 和 statement；主动操作使用完成状态表达时，也按该角色的一次动作处理，不能按瞬时状态过滤。角色是“系统”时，detail 中的系统事件、车辆状态和设备状态不能改写成用户动作，也不能因为 Episode 中有稳定用户引用就归因给该用户。最终 subject 仍按规则 7 使用 Episode 中的稳定用户引用。payload 只表达事件发生后的状态，不能把其中每个字段自动拆成独立动作。context 只描述环境和状态，不能单独证明主体动作。
3. 同一主体、同一相关过程、相同适用条件、相同 Evidence 和相同生命周期的连续动作可以放在一个元素中；否则分开。同一行为独立发生多次时，每次必须输出独立的 items 元素，不能合并依据或次数。
4. relations 是有序数组，按主张中的动作或关系顺序排列。predicate 单独阅读时必须说明关系或动作类型，并包含动作涉及的领域对象；拒绝只写“设置”“切换”“开启”“关闭”“调至”“操作”“处理”“发生变化”“相关”等宽泛名称。
5. predicate 使用输入的主要语言。每个 relation 自带可选 target；target 只保存具体对象、动作作用范围或结果值本身，不添加字段名、角色名或解释性前缀，没有自然目标时省略 target。
6. relations必须保留每个主动操作中用于区分该操作的语义，不能将具体动作泛化成更宽泛的动作类型；这些语义只出现在statement中，不算relations已完整表达。statement 完整表达全部 relations 及其顺序。condition 仅用于来源明确陈述的可复用 IF-THEN 规则、条件偏好或条件意图。一次已经完成的动作所处的时间、地点、环境、前后状态和可能原因，即使看起来相关，也不能写成 condition，只能保留在 statement 或省略。不能从单次行为归纳条件规律。
7. 尽可能完成 Episode 内的简单共指消解。有稳定用户引用时，subject 使用“用户 <引用>”。多人场景只有证据足够时才消解，不能强行判断。
8. raw_event_ids 使用输入中的 ID，按 Episode 顺序排列，不能重复。它们必须是共同支持完整 statement 和 condition 的最小集合。
9. statement 和 condition 各自只能使用一行文本，不能包含换行符。

entity_mentions 列出 statement 和 condition 中除完整 subject 外、具有专名或稳定标识的具体实体原文；没有则输出空数组。完整 relation target 可以作为候选列出，后续调用会单独判断它是否值得建立 Entity 身份。

最小 JSON 结构：
{"items":[{"subject":"文本","relations":[{"predicate":"文本"}],"statement":"文本","entity_mentions":["entity"],"raw_event_ids":["ID"]}]}

所有输出文本都必须包含实际内容，禁止空字符串。relation 有非空目标时才添加 target；主张有非空 IF 条件时才添加 condition。没有剩余 Entity 时输出 entity_mentions 空数组。没有合格主张时输出 {"items":[]}。
"""

TARGET_ENTITY_SYSTEM_PROMPT = """\
你只判断每个 target 的完整文本是否值得作为记忆中的 Entity。
只选择能够指向具体对象，并且其身份能区分记忆内容、可被其他记录再次引用的 target。
普通值、状态、模式、动作范围、方位、通用类别，以及没有提供可区分身份的通用界面页面都不选。
完整文本必须整体指向一个单一对象。用于限定该对象的名称组合可以选择；表达多个对象之间路线、区间或关系的文本不选。
示例1：
输入 {"targets":["海淀公园","公园","《夜曲》","周杰伦的《夜曲》","设置页"]}
输出 {"entity_target_indexes":[0,2,3]}
示例2：
输入 {"targets":["王小明","手臂","26℃","全部座椅","上海"]}
输出 {"entity_target_indexes":[0,4]}
示例3：
输入 {"targets":["王菲的《红豆》","从广州到深圳"]}
输出 {"entity_target_indexes":[0]}
输入是按位置排列的 target 字符串数组。只输出 JSON：
{"entity_target_indexes":[从0开始的整数索引]}
索引按输入顺序排列，不能重复。不要重复 target 文本，不要输出解释或其他字段。
"""


def build_claim_user_prompt(episode: EpisodeInput) -> str:
    """Serialize the complete ordered Episode as compact JSON."""

    return f"请抽取以下 EpisodeInput JSON：\n{episode.model_dump_json()}"


def build_target_entity_user_prompt(targets: list[str]) -> str:
    """Serialize ordered relation targets without Episode context."""

    return json.dumps(
        {"targets": targets},
        ensure_ascii=False,
        separators=(",", ":"),
    )
