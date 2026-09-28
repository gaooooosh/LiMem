"""Read-only operation environment and exact parameter queries."""

import json
from .models import SourceQuery, UnreadQuery

NAMES = {
    "seat.row1.left.ventilation_level": "主驾座椅通风档位",
    "cabin.media.volume": "媒体音量",
    "cabin.media.title": "媒体标题",
    "cabin.media.category": "媒体分类",
    "cabin.media.play_status": "媒体播放状态",
    "navigation.active": "导航是否开启",
    "navigation.destination": "导航目的地",
    "navigation.distance_km": "导航距离（公里）",
    "navigation.eta_minutes": "导航预计时长（分钟）",
    "cabin.screen.center.brightness_level": "中控屏亮度级别",
    "cabin.screen.center.content_mode": "中控屏内容模式",
    "hvac.airflow.airflow_mode": "空调出风模式",
    "hvac.zone.all.target_temp_c": "座舱空调目标温度（摄氏度）",
    "hvac.ac_unit.ac_mode": "空调模式",
    "hvac.airflow.fan_speed": "空调风量档位",
    "body.window.row1.left.open_pct": "主驾车窗开度百分比",
    "body.window.row1.right.open_pct": "副驾车窗开度百分比",
    "body.window.row2.left.open_pct": "后排左侧车窗开度百分比",
    "body.window.row2.right.open_pct": "后排右侧车窗开度百分比",
}


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def card_view(row, full=False):
    params = []
    for p in row["operation"]["parameters"]:
        item = dict(
            对象=NAMES[p["field"]],
            之前=p["before"],
            之后=p["after"],
            请求值=p["requested"],
        )
        if "delta" in p:
            item["变化量"] = p["delta"]
        params.append(item)
    view = dict(
        id=row["id"],
        关联Evidence=row["evidence"],
        操作原文=row["operation"]["detail"],
        参数=params,
    )
    if full:
        view["动作前完整观察"] = [
            dict(Evidence=c["evidence"], 原文=c["before"])
            for c in row["source_variants"]
        ]
    return view


def query_parameter(row, query):
    for p in row["operation"]["parameters"]:
        if NAMES[p["field"]] == query.field:
            if (
                query.position in ("before", "after")
                and not p[query.position + "_present"]
            ):
                return None
            if query.position in p:
                return p
    return None


def select(action, data, state):
    rows = data["records"]
    seen = state["visible"]
    query = action.query
    if isinstance(query, SourceQuery):
        ids = set(query.records)
        if len(ids) != len(query.records) or ids - set(r["id"] for r in rows):
            raise ValueError("source records must be distinct known IDs")
        return [r for r in rows if r["id"] in ids]
    unread = [r for r in rows if r["id"] not in seen]
    if isinstance(query, UnreadQuery):
        return unread
    fields = sorted(
        {NAMES[p["field"]] for r in rows for p in r["operation"]["parameters"]}
    )
    if query.field not in fields:
        raise ValueError(f"不支持查询字段：{query.field}；可查询字段：{fields}")
    selected = []
    for row in unread:
        p = query_parameter(row, query)
        if p is None:
            continue
        value = p[query.position]
        # JSON booleans must not match numeric 0/1.
        equal = (
            isinstance(value, bool) == isinstance(query.value, bool)
            and value == query.value
        )
        if equal == (query.operator == "eq"):
            selected.append(row)
    return selected
