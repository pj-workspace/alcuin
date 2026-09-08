"""Application service for Workspace-scoped, durable Agent Tasks.

Task orchestration deliberately sits above ``AgentRuntime``. A Task can span several
Runs, while provider adapters continue to expose the stable single-Run stream contract.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from alcuin_core.contracts import AgentDefinition, RunCreate
from alcuin_core.tasks import (
    TaskCommand,
    TaskCreate,
    TaskPlanUpdate,
)
from alcuin_storage import ControlPlaneRepository, RepositoryConflict

from ..config import Settings
from ..run_profile import resolve_run_model_controls


class TaskNotFound(LookupError):
    """Raised when a Task or its owning Thread is outside the current Workspace."""


class TaskStateConflict(RuntimeError):
    """Raised when a command targets a stale or incompatible Task state."""


DispatchCallback = Callable[[str, str], None]


class TaskService:
    """Authorize semantic Task commands and delegate atomic transitions to storage."""

    def __init__(
        self,
        repository: ControlPlaneRepository,
        *,
        settings: Settings | None = None,
        dispatch: DispatchCallback | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings if settings is not None else Settings()
        self._dispatch = dispatch

    def set_dispatch(self, dispatch: DispatchCallback) -> None:
        self._dispatch = dispatch

    def create(
        self,
        workspace_id: str,
        thread_id: str,
        payload: TaskCreate,
    ) -> dict[str, Any]:
        thread = self.repository.get_thread(workspace_id, thread_id)
        if thread is None:
            raise TaskNotFound("Thread not found")
        definition_record = self.repository.get_agent_version(
            workspace_id, str(thread["agent_version_id"])
        )
        if definition_record is None:
            raise TaskNotFound("Task Agent definition not found")
        # Validate before creating any durable resource. These same controls are
        # revalidated for every Step, including retries and restart recovery.
        resolve_run_model_controls(
            self.settings,
            AgentDefinition.model_validate(definition_record["definition"]),
            RunCreate(
                input=payload.goal,
                model_override=payload.model_override,
                reasoning_effort=payload.reasoning_effort,
            ),
        )
        try:
            task = self.repository.create_task(
                workspace_id,
                thread_id,
                payload.goal.strip(),
                model_override=payload.model_override,
                reasoning_effort=(
                    payload.reasoning_effort.value
                    if payload.reasoning_effort is not None
                    else None
                ),
            )
            if payload.steps:
                task = self.repository.replace_task_plan(
                    workspace_id,
                    str(task["id"]),
                    expected_revision=int(task["revision"]),
                    steps=[step.model_dump(mode="json") for step in payload.steps],
                )
        except RepositoryConflict as exc:
            raise TaskStateConflict(str(exc)) from exc
        return task

    def get(self, workspace_id: str, task_id: str) -> dict[str, Any]:
        task = self.repository.get_task(workspace_id, task_id)
        if task is None:
            raise TaskNotFound("Task not found")
        return task

    def list(
        self,
        workspace_id: str,
        *,
        thread_id: str | None = None,
        statuses: tuple[str, ...] = (),
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return self.repository.list_tasks(
            workspace_id,
            thread_id=thread_id,
            statuses=statuses,
            limit=limit,
        )

    def replace_plan(
        self,
        workspace_id: str,
        task_id: str,
        payload: TaskPlanUpdate,
    ) -> dict[str, Any]:
        self.get(workspace_id, task_id)
        try:
            task = self.repository.replace_task_plan(
                workspace_id,
                task_id,
                expected_revision=payload.expected_revision,
                steps=[step.model_dump(mode="json") for step in payload.steps],
                goal=payload.goal.strip() if payload.goal is not None else None,
            )
        except RepositoryConflict as exc:
            raise TaskStateConflict(str(exc)) from exc
        return task

    def command(
        self,
        workspace_id: str,
        task_id: str,
        payload: TaskCommand,
    ) -> dict[str, Any]:
        self.get(workspace_id, task_id)
        command_request = payload.model_dump(mode="json", exclude_none=True)
        try:
            command_result = self.repository.execute_task_command(
                workspace_id,
                task_id,
                command_request,
                include_replay_metadata=True,
            )
        except RepositoryConflict as exc:
            raise TaskStateConflict(str(exc)) from exc

        if self._dispatch is not None and not command_result["replayed"]:
            self._dispatch(workspace_id, task_id)
        # Idempotency preserves the command transition exactly once, while callers
        # still receive the latest resource projection if the Task has advanced.
        return self.get(workspace_id, task_id)
