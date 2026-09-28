"""Frozen six-decision behavior Agent. Explicit use only; no persistence."""

from copy import deepcopy
import json
from typing import Annotated, Literal, Union
from pydantic import Field, create_model
from . import environment, review as rederive
from .models import StrictModel, Propose, Inspect, SourceQuery, UnreadQuery, MatchQuery
from .prompts import SYSTEM

Review = rederive.Review


class Finish(StrictModel):
    tool: Literal["finish"]
    abandon: bool
    reason: str = Field(min_length=1)


class Stop(Finish):
    abandon: Literal[True]


ACTIONS = {
    "propose": [Propose, Stop],
    "inspect": [Inspect, Stop],
    "review": [Review],
    "ready": [Finish],
}


def step_model(data, state):
    phase = state["phase"]
    if phase == "review":
        return rederive.step_model(state)
    actions = ACTIONS[phase]
    visible_ids = list(state["visible"])
    all_ids = [r["id"] for r in data["records"]]
    if phase == "propose":
        proposal = create_model(
            "BoundPropose",
            __base__=Propose,
            condition=(Literal[None], ...),
            supports=(list[Literal[tuple(visible_ids)]], Field(min_length=1)),
        )
        actions = [proposal, Stop]
    elif phase == "inspect":
        source = create_model(
            "BoundSource",
            __base__=SourceQuery,
            records=(list[Literal[tuple(all_ids)]], Field(min_length=1)),
        )
        fields = {}
        for row in data["records"]:
            if row["id"] not in visible_ids:
                continue
            for p in row["operation"]["parameters"]:
                entry = fields.setdefault(
                    environment.NAMES[p["field"]], {"positions": set(), "types": set()}
                )
                for position in ("before", "after", "delta"):
                    present = (
                        p[position + "_present"]
                        if position in ("before", "after")
                        else position in p
                    )
                    if present:
                        entry["positions"].add(position)
                        entry["types"].add(type(p[position]))
        query_types = [UnreadQuery, source]
        for i, (name, entry) in enumerate(sorted(fields.items())):
            if not entry["positions"]:
                continue
            types = sorted(entry["types"], key=lambda t: t.__name__)
            value_type = types[0] if len(types) == 1 else Union[tuple(types)]
            query_types.append(
                create_model(
                    f"MatchField{i}",
                    __base__=MatchQuery,
                    field=(Literal[name], ...),
                    position=(Literal[tuple(sorted(entry["positions"]))], ...),
                    value=(value_type, ...),
                )
            )
        inspect = create_model(
            "BoundInspect",
            __base__=Inspect,
            query=(Union[tuple(query_types)], Field(discriminator=None)),
        )
        actions = [inspect, Stop]
    action_type = (
        actions[0]
        if len(actions) == 1
        else Annotated[Union[tuple(actions)], Field(discriminator="tool")]
    )
    return create_model(
        f"Step_{phase}",
        __base__=StrictModel,
        intent=(str, ...),
        action=(action_type, ...),
    )


def init_state(data):
    return dict(
        candidate=None,
        versions=[],
        latest=None,
        visible={r["id"]: environment.card_view(r) for r in data["records"][:2]},
        initial=[r["id"] for r in data["records"][:2]],
        phase="propose",
        current_question="",
        pending=[],
        last_review=None,
        reviews=[],
        empty_queries=[],
    )


def state_view(data, state, turn):
    candidate = (
        None
        if state["candidate"] is None
        else {k: v for k, v in state["candidate"].items() if k != "open_question"}
    )
    return dict(
        subject=data["subject"],
        operation_count=len(data["records"]),
        evidence_count=data["evidence_count"],
        available_records=[r["id"] for r in data["records"]],
        remaining_steps=6 - turn,
        phase=state["phase"],
        allowed_actions=[
            a.model_fields["tool"].annotation.__args__[0]
            for a in ACTIONS[state["phase"]]
        ],
        candidate=candidate,
        current_question=state["current_question"],
        pending_records=state["pending"],
        last_review=state["last_review"],
        latest_feedback=state["latest"],
        records=list(state["visible"].values()),
    )


def token_count(client, data, state, turn):
    user = environment.compact(
        rederive.view(data, state)
        if state["phase"] == "review"
        else state_view(data, state, turn)
    )
    system = rederive.SYSTEM if state["phase"] == "review" else SYSTEM
    count = client.post(
        "/tokenize",
        dict(
            model="Qwen3-1.7B",
            messages=[
                dict(role="system", content=system),
                dict(role="user", content=user),
            ],
            chat_template_kwargs={"enable_thinking": False},
        ),
    )["count"]
    return count, user


def failed(message):
    return dict(ok=False, progress="none", message=message)


def execute(action, data, state, client, turn):
    if action.tool not in [
        a.model_fields["tool"].annotation.__args__[0] for a in ACTIONS[state["phase"]]
    ]:
        return failed(f"当前阶段为{state['phase']}，该行动不可用。")
    if isinstance(action, Finish):
        if action.abandon:
            return dict(
                ok=True,
                finished=True,
                candidate=None,
                progress="abandoned",
                reason=action.reason,
            )
        if state["phase"] != "ready":
            return failed("候选尚未完成新来源核对，不能提交。")
        candidate = deepcopy(state["candidate"])
        candidate["supports"] = list(
            dict.fromkeys(candidate["supports"] + state["last_review"]["records"])
        )
        return dict(
            ok=True,
            finished=True,
            candidate=candidate,
            progress="finish",
            reason=action.reason,
        )
    if isinstance(action, Propose):
        if action.condition is not None:
            return failed("本轮condition必须为null。")
        if (
            not action.supports
            or len(set(action.supports)) != len(action.supports)
            or set(action.supports) - state["visible"].keys()
        ):
            return failed("候选依据必须是非空、不重复的已读操作编号。")
        candidate = action.model_dump(exclude={"tool"})
        state["candidate"] = candidate
        state["versions"].append(deepcopy(candidate))
        state["current_question"] = action.open_question
        state["phase"] = "inspect"
        return dict(
            ok=True,
            progress="candidate_update",
            message="候选已保存，下一步查询当前疑点。",
        )
    if isinstance(action, Review):
        if (
            len(set(action.records)) != len(action.records)
            or set(action.records) - state["visible"].keys()
            or not set(action.records).intersection(state["pending"])
        ):
            return failed("核对引用须为不重复的已读编号，并包含本次查询记录。")
        previous_behavior = state["candidate"]["behavior"]
        review = action.model_dump(exclude={"tool"})
        review["previous_behavior"] = previous_behavior
        state["last_review"] = review
        state["reviews"].append(deepcopy(review))
        candidate = dict(
            behavior=action.behavior,
            condition=None,
            supports=action.records,
            open_question=action.missing_information,
        )
        state["candidate"] = candidate
        changed = action.behavior != previous_behavior
        if changed:
            state["versions"].append(deepcopy(candidate))
        state["pending"] = []
        state["current_question"] = action.missing_information
        support_count = len(action.records)
        new_source = bool(set(action.records) - set(state["initial"]))
        state["phase"] = (
            "ready"
            if not action.missing_information and new_source and support_count >= 2
            else "inspect"
        )
        return dict(
            ok=True,
            progress="review_revision" if changed else "review",
            next_phase=state["phase"],
            new_source_reviewed=new_source,
            support_count=support_count,
            message="新行为与依据已保存，本轮查证完成，下一步结束。"
            if state["phase"] == "ready"
            else "新行为与依据已保存，继续查证缺失信息；无法判断时结束。",
        )
    query = action.query.model_dump()
    signature = json.dumps(
        [query, state["visible"]], ensure_ascii=False, sort_keys=True
    )
    if signature in state["empty_queries"]:
        return failed("相同来源范围内，该查询已返回空。请更换查询或结束。")
    try:
        selected = environment.select(action, data, state)
    except ValueError as exc:
        return failed(str(exc))
    before = deepcopy(state["visible"])
    returned = [r["id"] for r in selected]
    omitted = []
    for row in selected:
        state["visible"][row["id"]] = environment.card_view(
            row, action.query.selection == "source"
        )
    while True:
        state["pending"] = returned.copy()
        state["phase"] = "review" if returned else "inspect"
        feedback = dict(
            ok=True,
            progress="records_to_review" if returned else "none",
            executed_query=query,
            returned=returned.copy(),
            new_records=[rid for rid in returned if rid not in before],
            omitted_due_budget=omitted.copy(),
            message="记录已返回，下一步review核对当前候选。"
            if returned
            else "查询无匹配，尚未确认候选。",
        )
        if action.query.selection == "match":
            feedback["matched_values"] = [
                dict(
                    record=r["id"],
                    value=environment.query_parameter(r, action.query)[
                        action.query.position
                    ],
                )
                for r in selected
                if r["id"] in returned
            ]
        previous = state["latest"]
        state["latest"] = feedback
        count, _ = token_count(client, data, state, turn + 1)
        state["latest"] = previous
        if count <= 6000:
            if not returned and not selected:
                state["empty_queries"].append(signature)
            return feedback
        if not returned:
            raise ValueError(f"input state exceeds 6000 tokens: {count}")
        rid = returned.pop()
        omitted.insert(0, rid)
        if rid in before:
            state["visible"][rid] = before[rid]
        else:
            del state["visible"][rid]


def run(client, data):
    """Return the candidate, terminal status, state and complete decision trace."""
    state = init_state(data)
    trace = []
    for turn in range(6):
        count, user = token_count(client, data, state, turn)
        phase = state["phase"]
        system = rederive.SYSTEM if phase == "review" else SYSTEM
        model = step_model(data, state)
        step = client.call(
            system,
            user,
            model,
            purpose=f'{data["task"]}/policy/{turn}',
            input_limit=6000,
            output_limit=512,
        )
        action = step if phase == "review" else step.action
        observation = execute(action, data, state, client, turn)
        state["latest"] = observation
        trace.append(
            dict(
                turn=turn,
                phase=phase,
                intent=None if phase == "review" else step.intent,
                action=action.model_dump(),
                observation=observation,
                prompt_tokens=count,
            )
        )
        if observation.get("finished"):
            return dict(
                status="abandoned" if observation["candidate"] is None else "completed",
                candidate=observation["candidate"],
                decisions=len(trace),
                read_records=list(state["visible"]),
                state=state,
                trace=trace,
            )
    return dict(
        status="decision_budget_exhausted",
        candidate=None,
        decisions=len(trace),
        read_records=list(state["visible"]),
        state=state,
        trace=trace,
    )
