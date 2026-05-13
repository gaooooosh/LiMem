"""/db/{db_id}/api/entities/{entity_id}/patterns 注册实体 pattern 路由。

变更点（vs 旧版）：
- `GET /{pattern_id}` 显式把 store 返回的 dict 包装成 EntityPattern，避免与 response_model 不一致。
- `DELETE /{pattern_id}` 由 204 改为 200 + DeleteEntityPatternResponse，保留 action 信息。
- 新增 `POST :batch` 批量注册，支持原子（默认）与"尽力而为"两种模式。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth.dependencies import get_ltm_context, get_ltm_context_write
from ..models import (
    BatchCreateEntityPatternFailure,
    BatchCreateEntityPatternsRequest,
    BatchCreateEntityPatternsResponse,
    CreateEntityPatternRequest,
    DeleteEntityPatternResponse,
    EntityPattern,
    EntityPatternResponse,
    ListEntityPatternsResponse,
    UpdateEntityPatternRequest,
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


@router.get("", response_model=ListEntityPatternsResponse)
def list_entity_patterns(
    entity_id: str,
    q: str = Query(default="", description="Optional text query for pattern recall."),
    limit: int = Query(default=100, ge=1, le=500),
    include_inactive: bool = Query(default=False),
    handle: LtmHandle = Depends(get_ltm_context),
) -> ListEntityPatternsResponse:
    try:
        items = handle.ltm.list_entity_patterns(
            entity_id=entity_id,
            query=q,
            limit=limit,
            include_inactive=include_inactive,
        )
    except ValueError as exc:
        raise _not_found_from_value_error(exc) from exc
    return ListEntityPatternsResponse(
        items=[EntityPattern(**item) for item in items],
        total=len(items),
        query=q,
    )


# 注意：`:batch` 必须放在 `POST ""` 之前注册（FastAPI 按定义顺序匹配），
# 否则会被通配 `POST ""` 误匹配。
@router.post("/:batch", response_model=BatchCreateEntityPatternsResponse)
def batch_create_entity_patterns(
    entity_id: str,
    request: BatchCreateEntityPatternsRequest,
    handle: LtmHandle = Depends(get_ltm_context_write),
) -> BatchCreateEntityPatternsResponse:
    if not request.patterns:
        raise HTTPException(status_code=400, detail="patterns must not be empty")
    audit = handle.audit
    items = [p.model_dump() for p in request.patterns]
    with audit.trace(
        "entity_pattern_batch_create",
        {"entity_id": entity_id, "count": len(items), "atomic": request.atomic},
    ) as trace_id:
        with handle.write_lock:
            before = audit.graph_snapshot(handle.ltm)
            created: list[dict] = []
            failed: list[BatchCreateEntityPatternFailure] = []
            try:
                for idx, payload in enumerate(items):
                    try:
                        result = handle.ltm.create_entity_pattern(
                            entity_id=entity_id,
                            content=payload["content"],
                            pattern_type=payload.get("pattern_type") or "preference",
                            metadata=payload.get("metadata"),
                            pattern_id=payload.get("pattern_id"),
                        )
                        created.append(result["pattern"])
                    except ValueError as exc:
                        if request.atomic:
                            # 进入原子回滚：把前面成功创建的 pattern 全部硬删
                            _rollback_patterns(handle, entity_id, created)
                            raise _not_found_from_value_error(exc) from exc
                        failed.append(
                            BatchCreateEntityPatternFailure(
                                index=idx,
                                content=payload.get("content", ""),
                                error=str(exc),
                            )
                        )
                    except Exception as exc:  # noqa: BLE001 — 必须捕获以确保回滚
                        if request.atomic:
                            _rollback_patterns(handle, entity_id, created)
                            raise HTTPException(status_code=500, detail=str(exc)) from exc
                        failed.append(
                            BatchCreateEntityPatternFailure(
                                index=idx,
                                content=payload.get("content", ""),
                                error=str(exc),
                            )
                        )
            finally:
                audit.write(
                    trace_id,
                    "entity_pattern",
                    "batch_create_completed",
                    entity_type="entity",
                    entity_id=entity_id,
                    details={
                        "created_ids": [p["id"] for p in created],
                        "failed_count": len(failed),
                    },
                )
                after = audit.graph_snapshot(handle.ltm)
                audit.write_graph_delta(
                    trace_id, before, after, operation="entity_pattern_batch_create"
                )
    return BatchCreateEntityPatternsResponse(
        created=[EntityPattern(**p) for p in created],
        failed=failed,
        atomic=request.atomic,
    )


@router.get("/{pattern_id}", response_model=EntityPattern)
def get_entity_pattern(
    entity_id: str,
    pattern_id: str,
    handle: LtmHandle = Depends(get_ltm_context),
) -> EntityPattern:
    pattern = handle.ltm.get_entity_pattern(entity_id, pattern_id)
    if pattern is None:
        raise HTTPException(status_code=404, detail=f"Entity pattern not found: {pattern_id}")
    return EntityPattern(**pattern)


@router.post("", response_model=EntityPatternResponse)
def create_entity_pattern(
    entity_id: str,
    request: CreateEntityPatternRequest,
    handle: LtmHandle = Depends(get_ltm_context_write),
) -> EntityPatternResponse:
    audit = handle.audit
    payload = request.model_dump()
    with audit.trace("entity_pattern_create", {"entity_id": entity_id, **payload}) as trace_id:
        with handle.write_lock:
            before = audit.graph_snapshot(handle.ltm)
            try:
                result = handle.ltm.create_entity_pattern(
                    entity_id=entity_id,
                    content=request.content,
                    pattern_type=request.pattern_type,
                    metadata=request.metadata,
                    pattern_id=request.pattern_id,
                )
            except ValueError as exc:
                raise _not_found_from_value_error(exc) from exc
            audit.write(
                trace_id,
                "entity_pattern",
                "create_completed",
                entity_type="entity",
                entity_id=entity_id,
                details={"pattern_id": result["pattern"]["id"]},
            )
            after = audit.graph_snapshot(handle.ltm)
            audit.write_graph_delta(trace_id, before, after, operation="entity_pattern_create")
    return EntityPatternResponse(
        action=result["action"],
        pattern=EntityPattern(**result["pattern"]),
    )


@router.patch("/{pattern_id}", response_model=EntityPatternResponse)
def update_entity_pattern(
    entity_id: str,
    pattern_id: str,
    request: UpdateEntityPatternRequest,
    handle: LtmHandle = Depends(get_ltm_context_write),
) -> EntityPatternResponse:
    audit = handle.audit
    payload = request.model_dump(exclude_none=True)
    with audit.trace(
        "entity_pattern_update",
        {"entity_id": entity_id, "pattern_id": pattern_id, **payload},
    ) as trace_id:
        with handle.write_lock:
            before = audit.graph_snapshot(handle.ltm)
            try:
                result = handle.ltm.update_entity_pattern(
                    entity_id=entity_id,
                    pattern_id=pattern_id,
                    **payload,
                )
            except ValueError as exc:
                raise _not_found_from_value_error(exc) from exc
            audit.write(
                trace_id,
                "entity_pattern",
                "update_completed",
                entity_type="entity",
                entity_id=entity_id,
                details={"pattern_id": pattern_id},
            )
            after = audit.graph_snapshot(handle.ltm)
            audit.write_graph_delta(trace_id, before, after, operation="entity_pattern_update")
    return EntityPatternResponse(
        action=result["action"],
        pattern=EntityPattern(**result["pattern"]),
    )


@router.delete("/{pattern_id}", response_model=DeleteEntityPatternResponse)
def delete_entity_pattern(
    entity_id: str,
    pattern_id: str,
    hard_delete: bool = Query(default=False),
    handle: LtmHandle = Depends(get_ltm_context_write),
) -> DeleteEntityPatternResponse:
    audit = handle.audit
    with audit.trace(
        "entity_pattern_delete",
        {"entity_id": entity_id, "pattern_id": pattern_id, "hard_delete": hard_delete},
    ) as trace_id:
        with handle.write_lock:
            before = audit.graph_snapshot(handle.ltm)
            try:
                result = handle.ltm.delete_entity_pattern(
                    entity_id=entity_id,
                    pattern_id=pattern_id,
                    hard_delete=hard_delete,
                )
            except ValueError as exc:
                raise _not_found_from_value_error(exc) from exc
            audit.write(
                trace_id,
                "entity_pattern",
                "delete_completed",
                entity_type="entity",
                entity_id=entity_id,
                details={"pattern_id": pattern_id, "action": result["action"]},
            )
            after = audit.graph_snapshot(handle.ltm)
            audit.write_graph_delta(trace_id, before, after, operation="entity_pattern_delete")
    return DeleteEntityPatternResponse(
        action=result["action"],
        pattern=EntityPattern(**result["pattern"]),
    )


# ---------- 内部工具 ----------


def _rollback_patterns(
    handle: LtmHandle,
    entity_id: str,
    created: list[dict],
) -> None:
    """对一组已创建的 pattern 执行硬删除（容错：单条失败不阻断整体回滚）。"""
    for p in created:
        try:
            handle.ltm.delete_entity_pattern(
                entity_id=entity_id,
                pattern_id=p["id"],
                hard_delete=True,
            )
        except Exception:  # noqa: BLE001 — 回滚阶段尽最大努力
            continue
