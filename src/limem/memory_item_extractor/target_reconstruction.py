"""Fill missing relation targets from a frozen claim; retain all other fields."""
import json
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from limem.llm_client.qwen import QwenLLMClient
from .models import LLMClaimResult, _NonEmptyText
from .target_reconstruction_prompt import PROMPT


class ReconstructedTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target: _NonEmptyText | None


class TargetReconstructor:
    def __init__(self, client: QwenLLMClient, trace_root: Path) -> None:
        if client.settings.model != "qwen" or client.settings.temperature != 0 or client.settings.seed != 0:
            raise ValueError("target reconstruction requires the frozen base qwen settings")
        self.client = client
        self.trace_root = trace_root

    def reconstruct(self, result: LLMClaimResult, *, episode_id: str) -> LLMClaimResult:
        trace = self.trace_root / uuid4().hex
        trace.mkdir(parents=True)
        _save(trace / "input.json", {"episode_id": episode_id, "claims": result.model_dump(mode="json")})
        # The model always reads the original relations, including for later relations.
        output = result.model_dump(mode="json")
        try:
            for item_index, item in enumerate(result.items):
                for relation_index, relation in enumerate(item.relations):
                    if relation.target is not None:
                        continue
                    packed = {"statement": item.statement,
                              "relations": [r.model_dump(mode="json") for r in item.relations],
                              "relation_index": relation_index}
                    answer, _ = self.client.complete_plain_json(
                        PROMPT, json.dumps(packed, ensure_ascii=False), ReconstructedTarget,
                        max_tokens=256,
                        trace_dir=trace / f"item-{item_index:03d}-relation-{relation_index:03d}",
                    )
                    target = answer.target
                    if target is not None and target not in relation.predicate and target not in item.statement:
                        raise ValueError("reconstructed target is not a literal span of the current claim")
                    output["items"][item_index]["relations"][relation_index]["target"] = target
            reconstructed = LLMClaimResult.model_validate(output)
            _save(trace / "output.json", reconstructed.model_dump(mode="json"))
            return reconstructed
        except Exception as error:
            _save(trace / "error.json", {"type": type(error).__name__, "message": str(error)})
            raise


def _save(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
