"""Run explicit consolidation work without persisting job state."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
import logging
from dataclasses import dataclass, field
from threading import Event, Lock
from uuid import uuid4

from limem.consolidator import ConsolidationResult
from limem.memory_store import PendingConsolidationSubject

from .manager import MemoryBankManager
from .models import (
    ConsolidationRunFailure,
    ConsolidationRunResponse,
    ConsolidationRunResult,
    ConsolidatorType,
)
from .pipeline import (
    ConsolidationCancelled,
    ConsolidationStageError,
    MemoryServicePipeline,
)


class ConsolidationAlreadyRunningError(Exception):
    """The Bank already has an active manual or automatic consolidation."""


class ConsolidationRunNotFoundError(Exception):
    """The requested in-memory run does not exist for this Bank."""


class ConsolidationRunNotActiveError(Exception):
    """The requested run can no longer be cancelled."""


@dataclass
class _Run:
    run_id: str
    owner_key_id: str
    memory_bank_id: str
    consolidator_type: ConsolidatorType
    max_entities: int
    cancel_event: Event = field(default_factory=Event)
    status: str = "running"
    attempted_entities: int = 0
    current_subject_entity_id: str | None = None
    results: list[ConsolidationRunResult] = field(default_factory=list)
    stop_reason: str | None = None
    failure: ConsolidationRunFailure | None = None


class ConsolidationCoordinator:
    """Serialize consolidation per Bank and retain process-local run status."""

    def __init__(
        self,
        manager: MemoryBankManager,
        pipeline: MemoryServicePipeline,
        logger: logging.Logger,
    ) -> None:
        self._manager = manager
        self._pipeline = pipeline
        self._logger = logger
        self._lock = Lock()
        self._active_by_bank: dict[str, str] = {}
        self._runs: dict[str, _Run] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def consolidate_subject(
        self,
        owner_key_id: str,
        memory_bank_id: str,
        consolidator_type: ConsolidatorType,
        subject_entity_id: str,
    ) -> tuple[int, ConsolidationResult]:
        token = f"manual_{uuid4().hex}"
        self._claim_bank(memory_bank_id, token)
        try:
            with self._manager.use_owned_bank(owner_key_id, memory_bank_id) as (
                _,
                database_path,
            ):
                pending = self._pipeline.list_pending_consolidation_subjects(
                    database_path,
                    consolidator_type,
                )
                evidence_count = next(
                    (
                        subject.evidence_count
                        for subject in pending
                        if subject.subject_entity_id == subject_entity_id
                    ),
                    0,
                )
                result = self._pipeline.consolidate(
                    database_path,
                    consolidator_type,
                    subject_entity_id,
                )
                return evidence_count, result
        finally:
            self._release_bank(memory_bank_id, token)

    def start_run(
        self,
        owner_key_id: str,
        memory_bank_id: str,
        consolidator_type: ConsolidatorType,
        max_entities: int,
    ) -> ConsolidationRunResponse:
        run = _Run(
            run_id=f"conrun_{uuid4().hex}",
            owner_key_id=owner_key_id,
            memory_bank_id=memory_bank_id,
            consolidator_type=consolidator_type,
            max_entities=max_entities,
        )
        self._claim_bank(memory_bank_id, run.run_id)
        with self._lock:
            self._runs[run.run_id] = run
            self._tasks[run.run_id] = asyncio.create_task(self._run(run.run_id))
            return self._response(run)

    def get_run(
        self,
        memory_bank_id: str,
        run_id: str,
    ) -> ConsolidationRunResponse:
        with self._lock:
            return self._response(self._require_run(memory_bank_id, run_id))

    def cancel_run(
        self,
        memory_bank_id: str,
        run_id: str,
    ) -> ConsolidationRunResponse:
        with self._lock:
            run = self._require_run(memory_bank_id, run_id)
            if run.status == "cancel_requested":
                return self._response(run)
            if run.status != "running":
                raise ConsolidationRunNotActiveError(run_id)
            run.cancel_event.set()
            run.status = "cancel_requested"
            return self._response(run)

    @contextmanager
    def reserve_bank_for_deletion(self, memory_bank_id: str) -> Iterator[None]:
        """Prevent consolidation from starting while one Bank is deleted."""

        token = f"delete_{uuid4().hex}"
        self._claim_bank(memory_bank_id, token)
        try:
            yield
        finally:
            self._release_bank(memory_bank_id, token)

    async def shutdown(self) -> None:
        """Cancel active runs before closing the service repository."""

        with self._lock:
            for run in self._runs.values():
                if run.status in {"running", "cancel_requested"}:
                    run.cancel_event.set()
                    run.status = "cancel_requested"
            tasks = list(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks)

    async def _run(self, run_id: str) -> None:
        with self._lock:
            run = self._runs[run_id]
        try:
            pending = await asyncio.to_thread(self._list_pending_subjects, run)
            eligible = [
                subject
                for subject in pending
                if subject.evidence_count
                >= self._pipeline.minimum_evidence_count(run.consolidator_type)
            ]
            selected = eligible[: run.max_entities]
            if not selected:
                self._complete(run, "no_eligible_entities")
                return

            for subject in selected:
                if run.cancel_event.is_set():
                    self._cancel(run)
                    return
                with self._lock:
                    run.attempted_entities += 1
                    run.current_subject_entity_id = subject.subject_entity_id
                self._logger.info(
                    "自动巩固实体开始。运行 %s，记忆库 %s，实体 %s，Evidence %d 条。",
                    run.run_id,
                    run.memory_bank_id,
                    subject.subject_entity_id,
                    subject.evidence_count,
                )
                try:
                    result = await asyncio.to_thread(
                        self._consolidate_one,
                        run,
                        subject,
                    )
                except ConsolidationCancelled:
                    self._cancel(run)
                    return
                except ConsolidationStageError as exc:
                    self._fail(run, subject.subject_entity_id, exc.stage, exc.cause)
                    return

                outcome = "consolidated" if result.candidates else "no_consolidation"
                completed = ConsolidationRunResult(
                    subject_entity_id=subject.subject_entity_id,
                    pending_evidence_count=subject.evidence_count,
                    outcome=outcome,
                    candidate_memory_item_ids=[
                        candidate.id for candidate in result.candidates
                    ],
                )
                with self._lock:
                    run.results.append(completed)
                    run.current_subject_entity_id = None
                self._logger.info(
                    "自动巩固实体完成。运行 %s，实体 %s，结果 %s。",
                    run.run_id,
                    subject.subject_entity_id,
                    outcome,
                )

            stop_reason = (
                "max_entities_reached"
                if len(eligible) > run.max_entities
                else "no_eligible_entities"
            )
            self._complete(run, stop_reason)
        except Exception as exc:
            subject_entity_id = run.current_subject_entity_id or "unknown"
            self._fail(run, subject_entity_id, "run", exc)
        finally:
            self._release_bank(run.memory_bank_id, run.run_id)
            with self._lock:
                self._tasks.pop(run.run_id, None)

    def _list_pending_subjects(
        self,
        run: _Run,
    ) -> list[PendingConsolidationSubject]:
        with self._manager.use_owned_bank(
            run.owner_key_id,
            run.memory_bank_id,
        ) as (_, database_path):
            return self._pipeline.list_pending_consolidation_subjects(
                database_path,
                run.consolidator_type,
            )

    def _consolidate_one(
        self,
        run: _Run,
        subject: PendingConsolidationSubject,
    ) -> ConsolidationResult:
        with self._manager.use_owned_bank(
            run.owner_key_id,
            run.memory_bank_id,
        ) as (_, database_path):
            return self._pipeline.consolidate(
                database_path,
                run.consolidator_type,
                subject.subject_entity_id,
                run.cancel_event,
            )

    def _claim_bank(self, memory_bank_id: str, token: str) -> None:
        with self._lock:
            if memory_bank_id in self._active_by_bank:
                raise ConsolidationAlreadyRunningError(memory_bank_id)
            self._active_by_bank[memory_bank_id] = token

    def _release_bank(self, memory_bank_id: str, token: str) -> None:
        with self._lock:
            if self._active_by_bank.get(memory_bank_id) == token:
                del self._active_by_bank[memory_bank_id]

    def _require_run(self, memory_bank_id: str, run_id: str) -> _Run:
        run = self._runs.get(run_id)
        if run is None or run.memory_bank_id != memory_bank_id:
            raise ConsolidationRunNotFoundError(run_id)
        return run

    def _complete(self, run: _Run, stop_reason: str) -> None:
        with self._lock:
            run.status = "completed"
            run.stop_reason = stop_reason
            run.current_subject_entity_id = None
        self._logger.info(
            "自动巩固运行完成。运行 %s，记忆库 %s，原因 %s。",
            run.run_id,
            run.memory_bank_id,
            stop_reason,
        )

    def _cancel(self, run: _Run) -> None:
        with self._lock:
            run.status = "cancelled"
            run.current_subject_entity_id = None
        self._logger.info(
            "自动巩固运行取消。运行 %s，记忆库 %s。",
            run.run_id,
            run.memory_bank_id,
        )

    def _fail(
        self,
        run: _Run,
        subject_entity_id: str,
        stage: str,
        cause: Exception,
    ) -> None:
        message = str(cause).splitlines()[0].strip() or type(cause).__name__
        with self._lock:
            run.status = "failed"
            run.current_subject_entity_id = None
            run.failure = ConsolidationRunFailure(
                subject_entity_id=subject_entity_id,
                stage=stage,
                message=message,
            )
        self._logger.exception(
            "自动巩固运行失败。运行 %s，记忆库 %s，实体 %s，阶段 %s。",
            run.run_id,
            run.memory_bank_id,
            subject_entity_id,
            stage,
        )

    @staticmethod
    def _response(run: _Run) -> ConsolidationRunResponse:
        return ConsolidationRunResponse(
            run_id=run.run_id,
            memory_bank_id=run.memory_bank_id,
            consolidator_type=run.consolidator_type,
            max_entities=run.max_entities,
            status=run.status,
            attempted_entities=run.attempted_entities,
            current_subject_entity_id=run.current_subject_entity_id,
            results=list(run.results),
            stop_reason=run.stop_reason,
            failure=run.failure,
        )
