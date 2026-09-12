"""FastAPI routes for durable Task resources and replayable Task events."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

from alcuin_core.tasks import Task, TaskCommand, TaskCreate, TaskPlanUpdate, TaskStatus
from alcuin_core.tasks import TaskPlanProposal, TaskPlanProposalRequest
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from ..security import RequestScope, resolve_scope
from .service import TaskNotFound, TaskService, TaskStateConflict
from .planning import PlanningError


ScopeDependency = Annotated[RequestScope, Depends(resolve_scope)]


def _ensure_operator(scope: RequestScope) -> None:
    # Task orchestration currently belongs to Studio. The frozen Embed compatibility
    # surface is deliberately not expanded while the core Agent experience is hardened.
    if scope.embed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Task orchestration is unavailable to embedded sessions",
        )


def _task_error(error: Exception) -> HTTPException:
    if isinstance(error, TaskNotFound):
        return HTTPException(status_code=404, detail=str(error))
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "task_state_conflict", "message": str(error)},
    )


def _event_cursor(after: int, last_event_id: str | None) -> int:
    if last_event_id is None:
        return after
    normalized = last_event_id.strip()
    if not normalized or not normalized.isascii() or not normalized.isdecimal():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Last-Event-ID must be a non-negative Task event sequence",
        )
    return max(after, int(normalized))


def create_task_router(service: TaskService) -> APIRouter:
    router = APIRouter(tags=["tasks"])

    @router.post(
        "/v1/threads/{thread_id}/task-plan-proposals", response_model=TaskPlanProposal
    )
    async def propose_task_plan(
        thread_id: str,
        payload: TaskPlanProposalRequest,
        scope: ScopeDependency,
    ) -> TaskPlanProposal:
        _ensure_operator(scope)
        scope.require("run:create")
        try:
            return await service.propose_plan(scope.workspace_id, thread_id, payload)
        except TaskNotFound as exc:
            raise _task_error(exc) from exc
        except PlanningError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @router.post(
        "/v1/threads/{thread_id}/tasks",
        status_code=201,
        response_model=Task,
    )
    async def create_task(
        thread_id: str,
        payload: TaskCreate,
        scope: ScopeDependency,
    ) -> dict:
        _ensure_operator(scope)
        scope.require("run:create")
        try:
            return service.create(scope.workspace_id, thread_id, payload)
        except (TaskNotFound, TaskStateConflict) as exc:
            raise _task_error(exc) from exc

    @router.get("/v1/tasks", response_model=list[Task])
    async def list_tasks(
        scope: ScopeDependency,
        thread_id: str | None = None,
        status_filter: list[TaskStatus] = Query(default=[], alias="status"),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> list[dict]:
        _ensure_operator(scope)
        scope.require("run:read")
        return service.list(
            scope.workspace_id,
            thread_id=thread_id,
            statuses=tuple(item.value for item in status_filter),
            limit=limit,
        )

    @router.get("/v1/tasks/{task_id}", response_model=Task)
    async def get_task(task_id: str, scope: ScopeDependency) -> dict:
        _ensure_operator(scope)
        scope.require("run:read")
        try:
            return service.get(scope.workspace_id, task_id)
        except TaskNotFound as exc:
            raise _task_error(exc) from exc

    @router.patch("/v1/tasks/{task_id}/plan", response_model=Task)
    async def replace_task_plan(
        task_id: str,
        payload: TaskPlanUpdate,
        scope: ScopeDependency,
    ) -> dict:
        _ensure_operator(scope)
        scope.require("run:create")
        try:
            return service.replace_plan(scope.workspace_id, task_id, payload)
        except (TaskNotFound, TaskStateConflict) as exc:
            raise _task_error(exc) from exc

    @router.post("/v1/tasks/{task_id}/commands", response_model=Task)
    async def command_task(
        task_id: str,
        payload: TaskCommand,
        scope: ScopeDependency,
    ) -> dict:
        _ensure_operator(scope)
        scope.require("run:create")
        try:
            return service.command(scope.workspace_id, task_id, payload)
        except (TaskNotFound, TaskStateConflict) as exc:
            raise _task_error(exc) from exc

    @router.get("/v1/tasks/{task_id}/events")
    async def stream_task_events(
        task_id: str,
        request: Request,
        scope: ScopeDependency,
        after: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        _ensure_operator(scope)
        scope.require("run:read")
        try:
            initial = service.get(scope.workspace_id, task_id)
        except TaskNotFound as exc:
            raise _task_error(exc) from exc
        cursor = _event_cursor(after, last_event_id)

        async def event_stream():
            nonlocal cursor, initial
            idle_ticks = 0
            while True:
                if await request.is_disconnected():
                    return
                events = service.repository.list_task_events(
                    scope.workspace_id,
                    task_id,
                    after=cursor,
                    limit=500,
                )
                if events:
                    idle_ticks = 0
                    for event in events:
                        cursor = int(event["sequence"])
                        yield (
                            f"id: {cursor}\n"
                            f"event: {event['type']}\n"
                            f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                        )
                else:
                    idle_ticks += 1
                    initial = service.get(scope.workspace_id, task_id)
                    if initial["status"] in {"cancelled", "completed", "failed"}:
                        terminal_events = service.repository.list_task_events(
                            scope.workspace_id,
                            task_id,
                            after=cursor,
                            limit=500,
                        )
                        for event in terminal_events:
                            cursor = int(event["sequence"])
                            yield (
                                f"id: {cursor}\n"
                                f"event: {event['type']}\n"
                                f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                            )
                        yield "data: [DONE]\n\n"
                        return
                    if idle_ticks % 20 == 0:
                        yield ": heartbeat\n\n"
                    await asyncio.sleep(0.1)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
