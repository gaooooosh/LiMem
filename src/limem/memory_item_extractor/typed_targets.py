"""The model decides one target kind; the program retains the original target indexes."""
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .models import TargetEntityResult


class TargetKind(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["entity", "parameter", "other"]


TYPED_TARGET_PROMPT = """只判断一个target完整文本的类型，输出JSON kind。
entity：整体指向一个具体对象，有可区分身份，可在其他记忆中再次引用；专名、具体名字、作品、明确身份的地点或设备可以选择。
parameter：普通数值、温度、百分比、档位、等级、时间等参数值，没有独立实体身份。
other：普通状态、模式、动作范围、方位、通用类别、字段路径、无具体身份的通用界面页面、请求或多个对象的路线/关系。
完整文本必须整体指向单一对象才是entity；不能因为target排在第一个就选择它。不改写target，不补充对象。
"""

_EXAMPLES = [
    ("海淀公园", "entity"), ("公园", "other"), ("《夜曲》", "entity"),
    ("周杰伦的《夜曲》", "entity"), ("设置页", "other"), ("王小明", "entity"),
    ("手臂", "other"), ("26℃", "parameter"), ("全部座椅", "other"), ("上海", "entity"),
    ("王菲的《红豆》", "entity"), ("从广州到深圳", "other"),
]
TYPED_TARGET_PROMPT += "下列正负判断来自原有边界示例：\n" + "\n".join(
    "输入 " + json.dumps({"target": target}, ensure_ascii=False) +
    "\n输出 " + json.dumps({"kind": kind}, ensure_ascii=False)
    for target, kind in _EXAMPLES
)



class TypedTargetClassifier:
    """Classify real target text locally; retain its original position and trace every call."""

    def __init__(self, client, trace_root):
        self.client = client
        self.trace_root = trace_root

    def classify(self, targets, *, episode_id):
        from uuid import uuid4
        directory = self.trace_root / uuid4().hex
        directory.mkdir(parents=True)
        (directory / "input.json").write_text(json.dumps({
            "episode_id": episode_id, "targets": targets,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        chosen = []
        for index, target in enumerate(targets):
            result, _ = self.client.complete_json(
                TYPED_TARGET_PROMPT, json.dumps({"target": target}, ensure_ascii=False),
                TargetKind, trace_dir=directory / f"target-{index:02d}",
            )
            if result.kind == "entity":
                chosen.append(index)
        result = TargetEntityResult(entity_target_indexes=chosen)
        (directory / "output.json").write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return result
