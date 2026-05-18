"""/db/{db_id}/api/entities/{entity_id}/patterns 注册实体 pattern 路由（v2）。

v2 模型：每个 entity 至多对应 1 篇 markdown pattern 文档。CRUD 收敛为 4 个端点：

- PUT    ""          upsert 整篇 markdown
- GET    ""          读取 pattern（无则 pattern=null）
- GET    "/recall"   召回（H2 切片 + 朴素打分；mode=auto|full|section）
- DELETE ""          硬删除 pattern

变更摘要（vs v1）：
- 删除：POST、POST :batch、GET /{pid}、PATCH /{pid}、DELETE /{pid}、GET 列表语义
- 删除：pattern_type / metadata / pattern_id / batch / archived 软删
- 路由路径保留复数 /patterns（路径里不再带 {pattern_id}）

写操作沿用 audit.trace + write_lock + graph_snapshot 模板。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth.dependencies import get_ltm_context, get_ltm_context_write
from ..models import (
    DeleteEntityPatternResponse,
    EntityPattern,
    MatchedSection,
    PutEntityPatternRequest,
    PutEntityPatternResponse,
    RecallEntityPatternResponse,
)
from ..pool import LtmHandle


router = APIRouter(
    prefix="/db/{db_id}/api/entities/{entity_id}/patterns",
    tags=["entity-patterns"],
)


def _not_found_from_value_error(exc: ValueError) -> HTTPException:
    msg = str(exc)
    if "not found" in msg.lower():
        return HTTPException(status_code=404, detail=msg)
    return HTTPException(status_code=400, detail=msg)


@router.put("", response_model=PutEntityPatternResponse)
def put_entity_pattern(
    entity_id: str,
    request: PutEntityPatternRequest,
    handle: LtmHandle = Depends(get_ltm_context_write),
) -> PutEntityPatternResponse:
    audit = handle.audit
    with audit.trace(
        "entity_pattern_put",
        {"entity_id": entity_id, "content_chars": len(request.content)},
    ) as trace_id:
        with handle.write_lock:
            before = audit.graph_snapshot(handle.ltm)
            try:
                result = handle.ltm.put_entity_pattern(
                    entity_id=entity_id, content=request.content
                )
            except ValueError as exc:
                raise _not_found_from_value_error(exc) from exc
            audit.write(
                trace_id,
                "entity_pattern",
                "put_completed",
                entity_type="entity",
                entity_id=entity_id,
                details={
                    "action": result["action"],
                    "pattern_id": result["pattern"]["id"],
                },
            )
            after = audit.graph_snapshot(handle.ltm)
            audit.write_graph_delta(trace_id, before, after, operation="entity_pattern_put")
    return PutEntityPatternResponse(
        action=result["action"],
        pattern=EntityPattern(**result["pattern"]),
    )


# 路由顺序：`/recall` 必须先于 `""` 的 GET 注册，避免被通配吞掉。FastAPI 按定义顺序匹配。
@router.get("/recall", response_model=RecallEntityPatternResponse)
def recall_entity_pattern(
    entity_id: str,
    query: str = Query(default="", description="召回查询字符串；空字符串走 full。"),
    mode: str = Query(default="auto", pattern="^(auto|full|section)$"),
    top_k_sections: int = Query(default=0, ge=0, le=20, description="0=使用 config 默认"),
    handle: LtmHandle = Depends(get_ltm_context),
) -> RecallEntityPatternResponse:
    result = handle.ltm.recall_entity_pattern(
        entity_id=entity_id,
        query=query,
        mode=mode,
        top_k_sections=top_k_sections,
    )
    if result is None:
        # 无 pattern：返回空响应而非 404，与 GET "" 行为一致。
        return RecallEntityPatternResponse(
            mode="full",
            content="",
            total_chars=0,
            matched_sections=[],
            pattern=None,
        )
    return RecallEntityPatternResponse(
        mode=result["mode"],
        content=result["content"],
        total_chars=result["total_chars"],
        matched_sections=[MatchedSection(**s) for s in result["matched_sections"]],
        pattern=EntityPattern(**result["pattern"]),
    )


@router.get("", response_model=RecallEntityPatternResponse)
def get_entity_pattern(
    entity_id: str,
    handle: LtmHandle = Depends(get_ltm_context),
) -> RecallEntityPatternResponse:
    """读取实体 pattern；无则 pattern=null + content=""。

    复用 RecallEntityPatternResponse 作为响应模型：mode=full、matched_sections=[]、
    content 即整篇 markdown。这样客户端只需处理一种响应形态。
    """
    pattern = handle.ltm.get_entity_pattern(entity_id)
    if pattern is None:
        return RecallEntityPatternResponse(
            mode="full",
            content="",
            total_chars=0,
            matched_sections=[],
            pattern=None,
        )
    return RecallEntityPatternResponse(
        mode="full",
        content=pattern["content"],
        total_chars=len(pattern["content"] or ""),
        matched_sections=[],
        pattern=EntityPattern(**pattern),
    )


@router.delete("", response_model=DeleteEntityPatternResponse)
def delete_entity_pattern(
    entity_id: str,
    handle: LtmHandle = Depends(get_ltm_context_write),
) -> DeleteEntityPatternResponse:
    audit = handle.audit
    with audit.trace(
        "entity_pattern_delete",
        {"entity_id": entity_id},
    ) as trace_id:
        with handle.write_lock:
            before = audit.graph_snapshot(handle.ltm)
            try:
                deleted = handle.ltm.delete_entity_pattern(entity_id=entity_id)
            except ValueError as exc:
                raise _not_found_from_value_error(exc) from exc
            if deleted is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Entity pattern not found for entity: {entity_id}",
                )
            audit.write(
                trace_id,
                "entity_pattern",
                "delete_completed",
                entity_type="entity",
                entity_id=entity_id,
                details={"pattern_id": deleted["id"]},
            )
            after = audit.graph_snapshot(handle.ltm)
            audit.write_graph_delta(trace_id, before, after, operation="entity_pattern_delete")
    return DeleteEntityPatternResponse(pattern=EntityPattern(**deleted))
