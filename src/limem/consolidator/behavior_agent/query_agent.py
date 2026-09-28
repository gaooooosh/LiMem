"""Behavior Agent with explicit nonempty query choices, preserving other phases."""

from copy import deepcopy
from typing import Literal, Union
from pydantic import Field, create_model
from . import agent as base
from . import environment
from .models import StrictModel, Inspect

SYSTEM = base.SYSTEM
start = SYSTEM.index("inspect：")
end = SYSTEM.index("\n行动规范：", start)
SYSTEM = (
    SYSTEM[:start]
    + """inspect：从available_queries选择query_id。每项包含实际查询、命中编号和数量。查询unread比较未读操作；source读取指定操作完整原文；match比较指定参数。根据current_question选择能够解决疑点的查询，不需要猜测未出现的参数值。命中只表示取到记录，下一步仍须review。
"""
    + SYSTEM[end:]
)


def query_menu(data, state):
    unread = [r for r in data["records"] if r["id"] not in state["visible"]]
    queries = []
    if unread:
        queries.append(dict(selection="unread"))
    fields = {
        environment.NAMES[p["field"]]
        for r in data["records"]
        if r["id"] in state["visible"]
        for p in r["operation"]["parameters"]
    }
    for row in unread:
        for p in row["operation"]["parameters"]:
            name = environment.NAMES[p["field"]]
            if name not in fields:
                continue
            for position in ("before", "after", "delta"):
                if position not in p or (
                    position != "delta" and not p[position + "_present"]
                ):
                    continue
                for operator in ("eq", "ne"):
                    queries.append(
                        dict(
                            selection="match",
                            field=name,
                            position=position,
                            operator=operator,
                            value=p[position],
                        )
                    )
    menu = []
    seen = set()
    for query in queries:
        action = Inspect.model_validate(dict(tool="inspect", query=query))
        records = [r["id"] for r in environment.select(action, data, state)]
        signature = tuple(records)
        if not records or signature in seen:
            continue
        seen.add(signature)
        menu.append(
            dict(
                query_id=f"Q{len(menu)+1}",
                query=query,
                records=records,
                count=len(records),
            )
        )
    for row in data["records"]:
        menu.append(
            dict(
                query_id=f"Q{len(menu)+1}",
                query=dict(selection="source", records=[row["id"]]),
                records=[row["id"]],
                count=1,
            )
        )
    return menu


def run(client, data):
    state = base.init_state(data)
    trace = []
    for turn in range(6):
        phase = state["phase"]
        if phase == "inspect":
            menu = query_menu(data, state)
            view = base.state_view(data, state, turn)
            view["available_queries"] = menu
            user = environment.compact(view)
            system = SYSTEM
            select = create_model(
                "SelectQuery",
                __base__=StrictModel,
                tool=(Literal["inspect"], ...),
                query_id=(Literal[tuple(x["query_id"] for x in menu)], ...),
            )
            model = create_model(
                "QueryDecision",
                __base__=StrictModel,
                intent=(str, ...),
                action=(Union[select, base.Stop], Field(discriminator="tool")),
            )
        else:
            _, user = base.token_count(client, data, state, turn)
            system = base.rederive.SYSTEM if phase == "review" else base.SYSTEM
            model = base.step_model(data, state)
        step = client.call(
            system,
            user,
            model,
            purpose=f'{data["task"]}/policy/{turn}',
            input_limit=6000,
            output_limit=512,
        )
        action = step if phase == "review" else step.action
        request = action.model_dump()
        if phase == "inspect" and action.tool == "inspect":
            query = next(x for x in menu if x["query_id"] == action.query_id)["query"]
            action = Inspect.model_validate(dict(tool="inspect", query=query))
        observation = base.execute(action, data, state, client, turn)
        state["latest"] = observation
        trace.append(
            dict(
                turn=turn,
                phase=phase,
                intent=None if phase == "review" else step.intent,
                action=request,
                executed_action=action.model_dump(),
                observation=deepcopy(observation),
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
