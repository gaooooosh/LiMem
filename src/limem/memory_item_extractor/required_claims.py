"""Known source-confirmed actions are fixed slots; semantic contents remain model outputs."""
import json
from typing import Literal

from pydantic import ConfigDict, Field, create_model

from .claim_generator import IndexedItem, IndexedResult, materialize, source_view
from .models import Relation, _ClaimText, _NonEmptyText


def slot_name(index, action):
    return f"source_{index}__" + action["action_code"].replace(".", "_")


def slot_schema(view):
    count = len(view["events"])
    fields = {}
    for position, action in enumerate(view["required_actions"]):
        index = action["source_index"]
        legal = tuple(value for value in range(count) if value != index)
        support_type = list[Literal.__getitem__(legal)] if legal else list[int]
        support_field = Field(default_factory=list) if legal else Field(default_factory=list, max_length=0)
        item = create_model(f"RequiredClaim{position}", __config__=ConfigDict(extra="forbid", strict=True),
            relations=(list[Relation], Field(min_length=1)), statement=(_ClaimText, ...),
            condition=(_ClaimText | None, None), entity_mentions=(list[_NonEmptyText], Field(default_factory=list)),
            support_indices=(support_type, support_field))
        fields[slot_name(index, action)] = (list[item], Field(min_length=1,
            description=f"只包装来源{index}中已经确认的{action['action_code']}及同一Record直接支持的人的动作；主语由程序固定为{action['subject']}"))
    required = create_model("RequiredClaims", __config__=ConfigDict(extra="forbid", strict=True), **fields)
    return create_model("SlottedResult", __config__=ConfigDict(extra="forbid", strict=True),
                        required_claims=(required, ...), additional_items=(list[IndexedItem], ...))


def slotted_prompt(original):
    marker = "以下示例全部来自独立train，表示边界和格式，不是本次事实：\n"
    prefix, encoded = original.split(marker, 1)
    examples = json.loads(encoded)
    for example in examples:
        actions = example["input"]["required_actions"]
        buckets = {slot_name(action["source_index"], action): [] for action in actions}
        additional = []
        for item in example["output"]["items"]:
            action = next((action for action in actions if action["source_index"] in item["source_indices"]
                           and action["subject"] == item["subject"]), None)
            if action is None:
                additional.append(item)
            else:
                buckets[slot_name(action["source_index"], action)].append({
                    **{key: value for key, value in item.items() if key not in ["subject", "source_indices"]},
                    "support_indices": [value for value in item["source_indices"] if value != action["source_index"]]})
        example["output"] = {"required_claims": buckets, "additional_items": additional}
    contract = """\n本轮输出合同：输出required_claims和additional_items两部分。required_claims的每个固定键对应输入已经确认的Action，必须填写非空主张列表。键名说明主来源与action_code；程序固定其subject与主来源，模型仅写语义及额外support_indices。先读取该键对应source_index的action正文，不要拿其他来源的状态差分替代它。
每个必填槽可以有多个独立主张，但每条均须由该Record直接支持；同一次动作不要再写到additional_items。additional_items仍按原语义规则保存其他真实主张，结构与原items相同。没有required_actions时required_claims={}，仍要读取正文判断其他主张，不能当作没有人的行为。
两部分合起来形成完整LLMClaimResult。主来源与主体固定不代表开放语义可以随意编写；参数、对象、请求/完成性质和引用真实性要求完全不变。\n"""
    return prefix + contract + marker + json.dumps(examples, ensure_ascii=False)


def generate_slotted(client, episode, user_ref, original_prompt, directory):
    view = source_view(episode, user_ref)
    result, _ = client.complete_json(
        slotted_prompt(original_prompt), json.dumps(view, ensure_ascii=False), slot_schema(view), trace_dir=directory)
    output = []
    for action in view["required_actions"]:
        index = action["source_index"]
        for item in getattr(result.required_claims, slot_name(index, action)):
            if len(item.support_indices) != len(set(item.support_indices)):
                raise ValueError("Repeated support source")
            output.append({"subject": action["subject"], **item.model_dump(exclude={"support_indices"}),
                           "source_indices": [index, *item.support_indices]})
    output.extend(item.model_dump() for item in result.additional_items)
    return materialize(episode, IndexedResult.model_validate({"items": output}))
