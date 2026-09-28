"""Natural-language prompt templates for repeated-behavior consolidation."""

from collections.abc import Sequence
import re

from limem.episode_builder import EpisodeInput
from limem.memory_store import Evidence, MemoryItem, MemoryRelation

from .models import CandidateProposal

CANDIDATE_DISCOVERY_SYSTEM_PROMPT = """\
这些观察来自同一用户多次发生的行为。请找出由至少两条Evidence支持的重复行为，每种行为输出一个candidate。
每种重复行为都概括完整动作：对什么对象，做了什么，结果是什么。具体名称不同时，概括共同类别；共同出现的多个动作按顺序保留；数值变化相同时保留变化量。actions是共同动作序列，statement用一句话描述该完整行为，不含发生条件。
每个candidate引用全部实际支持它的Evidence，并选择各Evidence中支撑动作的驾驶员原文短句编号。标签及编号只能来自输入。同一Evidence可以支持不同候选；没有可靠重复行为时输出空candidates。只输出JSON，不输出Condition或Entity字段。
当复合动作只在部分Evidence共现，而各子动作有更多独立支持时，只输出支持更广的独立candidate；若各条支持均包含完整复合动作，则保留复合candidate。
结构：{"candidates":[{"actions":["共同动作"],"statement":"共同完整行为","supports":[{"evidence":"E1","action_clause_indices":[1]}]}]}


示例1输入：
Evidence E1
1. 在青禾书店购买了《植物图鉴》
Evidence E2
1. 在知行书店购买了《世界建筑史》
Evidence E3
1. 在星河书店购买了《天文入门》
输出：
{"candidates": [{"actions": ["在书店购买图书"], "statement": "在书店购买图书。", "supports": [{"evidence": "E1", "action_clause_indices": [1]}, {"evidence": "E2", "action_clause_indices": [1]}, {"evidence": "E3", "action_clause_indices": [1]}]}]}

示例2输入：
Evidence E1
1. 解锁平板
2. 继续编辑上次未完成的《春季采购清单》文档
Evidence E2
1. 解锁平板
2. 继续编辑上次未完成的《暑期行程》文档
Evidence E3
1. 解锁平板
2. 继续编辑上次未完成的《搬家计划》文档
输出：
{"candidates": [{"actions": ["解锁平板", "继续编辑上次未完成的文档"], "statement": "解锁平板，并继续编辑上次未完成的文档。", "supports": [{"evidence": "E1", "action_clause_indices": [1, 2]}, {"evidence": "E2", "action_clause_indices": [1, 2]}, {"evidence": "E3", "action_clause_indices": [1, 2]}]}]}

示例3输入：
Evidence E1
1. 将投影仪亮度从12调至16
Evidence E2
1. 将投影仪亮度从15调至19
Evidence E3
1. 将投影仪亮度从18调至22
输出：
{"candidates": [{"actions": ["将投影仪亮度提高4"], "statement": "将投影仪亮度提高4。", "supports": [{"evidence": "E1", "action_clause_indices": [1]}, {"evidence": "E2", "action_clause_indices": [1]}, {"evidence": "E3", "action_clause_indices": [1]}]}]}"""


CONDITION_SYNTHESIS_SYSTEM_PROMPT = """\
你负责归纳给定候选动作发生时的 Condition。只输出一个 JSON 对象，不要输出解释或 Markdown。

输入包含候选动作和固定 Evidence 组的动作前观察。facts 中每项只表达一个与候选动作有关的条件事实。

规则：
1. 允许部分 Evidence 支持条件，不要求全部支持。每个事实选择的不同 Evidence 数量必须超过输入总数的50%。多项事实组合为完整条件时，共同支持所有事实的 Evidence 也必须超过50%。不要改变分母或重复计算同一标签。
2. supports 显式写出支持该事实的 Evidence 标签，以及该 Evidence 中的原文短句编号。不支持的 Evidence 不写入 supports，编号只来自对应 Evidence。
3. 名称不同时概括共同类别；数值不同时保留所选支持来源的最小值至最大值。不要输出原文片段。

输出结构：
{"facts":[{"text":"一个条件事实","supports":[{"evidence":"E1","clause_indices":[1]},{"evidence":"E3","clause_indices":[2]}]}]}
"""


def _render_relation(relation: MemoryRelation) -> str:
    text = relation.predicate
    if relation.target is not None:
        text += f"；目标：{relation.target}"
    if relation.target_entity_id is not None:
        text += f"；目标实体：{relation.target_entity_id}"
    return f"- {text}"


def driver_action_clauses(session_text: str) -> list[str]:
    actions = [
        line for line in session_text.splitlines()[2:] if " Action: 驾驶员：" in line
    ]
    if len(actions) != 1:
        raise ValueError(f"expected one driver action, got {len(actions)}")
    action = actions[0].split(" Action: 驾驶员：", 1)[1]
    return [part.strip() for part in re.split(r"[，；。]", action) if part.strip()]


def _evidence_action_lines(evidence: Evidence, session_text: str) -> list[str]:
    action_prefixes = tuple(
        f"[{raw_event_id}] Action:" for raw_event_id in evidence.raw_event_ids
    )
    return [
        line
        for line in session_text.splitlines()[2:]
        if line.startswith(action_prefixes)
    ]


def discovery_action_session(evidence: Evidence, session_text: str) -> str:
    lines = session_text.splitlines()
    action_lines = _evidence_action_lines(evidence, session_text)
    if not action_lines:
        raise ValueError(f"Evidence {evidence.id} has no source Action lines")
    return "\n".join([*lines[:2], *action_lines])


def evidence_has_driver_action(evidence: Evidence, session_text: str) -> bool:
    return any(
        " Action: 驾驶员：" in line
        for line in _evidence_action_lines(evidence, session_text)
    )


def evidence_driver_action_clauses(
    evidence: Evidence,
    session_text: str,
) -> list[str]:
    return driver_action_clauses(discovery_action_session(evidence, session_text))


def build_candidate_discovery_user_prompt(
    memory_items: Sequence[MemoryItem],
    evidence: Sequence[Evidence],
    labels_by_evidence_id: dict[str, str],
    session_text_by_label: dict[str, str],
) -> str:
    """Render every available observation without a prior grouping decision."""

    items_by_id = {item.id: item for item in memory_items}
    item_positions = {
        item.id: position for position, item in enumerate(memory_items, start=1)
    }
    blocks = []
    action_clauses = []
    for source in evidence:
        label = labels_by_evidence_id[source.id]
        item = items_by_id[source.memory_item_id]
        condition = item.condition if item.condition is not None else "无"
        blocks.append(
            "\n".join(
                [
                    f"观察 {label}",
                    f"MemoryItem M{item_positions[item.id]}",
                    f"主张：{item.statement}",
                    f"原条件：{condition}",
                    "行为：",
                    *[_render_relation(relation) for relation in item.relations],
                    "完整 Session：",
                    discovery_action_session(source, session_text_by_label[label]),
                ]
            )
        )
        action_clauses.extend(
            [
                f"Evidence {label}",
                *[
                    f"{index}. {clause}"
                    for index, clause in enumerate(
                        evidence_driver_action_clauses(
                            source,
                            session_text_by_label[label],
                        ),
                        start=1,
                    )
                ],
            ]
        )
    observations = "请从以下全部观察直接归纳重复行为规律：\n\n" + "\n\n".join(blocks)
    return "\n".join(
        [
            observations,
            "",
            "驾驶员 Action 原文短句：",
            *action_clauses,
        ]
    )


def _event_id(line: str) -> str:
    return line.split("]", 1)[0].removeprefix("[")


def pre_action_observation_clauses(session_text: str) -> list[str]:
    lines = session_text.splitlines()[2:]
    driver_actions = [line for line in lines if " Action: 驾驶员：" in line]
    if len(driver_actions) != 1:
        raise ValueError(f"expected one driver action, got {len(driver_actions)}")
    driver_position = lines.index(driver_actions[0])
    system_actions = [
        line for line in lines[:driver_position] if " Action: 系统：" in line
    ]
    if system_actions:
        return [
            part.strip()
            for line in system_actions
            for part in re.split(r"[，；。]", line.split(" Action: 系统：", 1)[1])
            if part.strip()
        ]

    driver_event_id = _event_id(driver_actions[0])
    contexts = [line for line in lines[:driver_position] if " Context: " in line]
    pre_contexts = [line for line in contexts if _event_id(line) != driver_event_id]
    post_contexts = [line for line in contexts if _event_id(line) == driver_event_id]
    if not pre_contexts or len(post_contexts) != 1:
        raise ValueError("cannot identify pre-action and post-action Context")
    pre_text = pre_contexts[-1].split(" Context: ", 1)[1]
    post_text = post_contexts[0].split(" Context: ", 1)[1]
    pre_clauses = [
        part.strip() for part in re.split(r"[；。]", pre_text) if part.strip()
    ]
    post_clauses = {
        part.strip() for part in re.split(r"[；。]", post_text) if part.strip()
    }
    changed = [clause for clause in pre_clauses[1:] if clause not in post_clauses]
    if not changed:
        raise ValueError("pre-action Context has no changed clauses")
    return changed


def build_condition_synthesis_user_prompt(
    candidate: CandidateProposal,
    session_text_by_label: dict[str, str],
) -> str:
    """Render only pre-action observations for one fixed candidate."""

    lines = [
        "请归纳以下固定 support 组的 Condition。",
        "候选动作：",
        *[f"{index}. {action}" for index, action in enumerate(candidate.actions, start=1)],
        f"支持门槛：共{len(candidate.supports)}条Evidence，完整条件至少需要{len(candidate.supports) // 2 + 1}条共同支持。",
        "动作前观察：",
    ]
    for support in candidate.supports:
        clauses = pre_action_observation_clauses(
            session_text_by_label[support.evidence]
        )
        lines.extend(
            [
                f"Evidence {support.evidence}",
                *[
                    f"{index}. {clause}"
                    for index, clause in enumerate(clauses, start=1)
                ],
            ]
        )
    return "\n".join(lines)


def render_evidence_session(
    label: str,
    evidence: Evidence,
    episode: EpisodeInput,
) -> str:
    """Render the complete EpisodeBuilder output used for fragment checks."""

    lines = [f"Evidence {label}", f"Session {evidence.source_session_id}"]
    for event in episode.events:
        lines.append(f"[{event.raw_event_id}] Context: {event.context}")
        if event.action is not None:
            lines.append(f"[{event.raw_event_id}] Action: {event.action}")
    return "\n".join(lines)
