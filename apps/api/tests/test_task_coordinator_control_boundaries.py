from __future__ import annotations

import asyncio
from typing import Any

import pytest

from alcuin_api.tasks import TaskCoordinator, TaskService
from alcuin_core.tasks import TaskCommand, TaskCreate, TaskStepDraft
from alcuin_storage import PostgresStore
from support import create_test_store


WORKSPACE_ID = "ws_demo"


class _ControlledTerminalRun:
    """Hold one active Run until a Task control request is durable."""

    def __init__(self, store: PostgresStore, run_status: str) -> None:
        self.store = store
        self.run_status = run_status
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(
        self,
        workspace_id: str,
        task: dict[str, Any],
        step: dict[str, Any],
        attempt_id: str,
    ) -> dict[str, Any]:
        thread = self.store.get_thread(workspace_id, str(task["thread_id"]))
        assert thread is not None
        run = self.store.create_run(
            workspace_id,
            str(task["thread_id"]),
            str(thread["agent_version_id"]),
            "Execute the controlled Task step",
        )
        self.store.link_task_run(
            workspace_id,
            str(task["id"]),
            str(step["id"]),
            attempt_id,
            str(run["id"]),
        )
        self.store.set_run_status(workspace_id, str(run["id"]), "running")
        self.started.set()
        await self.release.wait()

        if self.run_status == "waiting_for_approval":
            approval = self.store.create_approval(
                workspace_id,
                str(run["id"]),
                {
                    "tool": "test.write",
                    "call_id": "call_control_boundary",
                    "arguments": {"record_id": "REC-control-boundary"},
                },
            )
            self.store.append_event(
                workspace_id,
                str(run["id"]),
                "approval.required",
                {"approval_id": approval["id"], "tool": "test.write"},
            )
        self.store.set_run_status(workspace_id, str(run["id"]), self.run_status)
        materialized = self.store.get_run(workspace_id, str(run["id"]))
        assert materialized is not None
        return materialized


def _create_task(store: PostgresStore) -> dict[str, Any]:
    agent = store.list_agents(WORKSPACE_ID)[0]
    thread = store.create_thread(
        WORKSPACE_ID,
        str(agent["id"]),
        "Task control boundary test",
        {},
    )
    return TaskService(store).create(
        WORKSPACE_ID,
        str(thread["id"]),
        TaskCreate(
            goal="Keep operator control authoritative at the active Run boundary.",
            steps=[TaskStepDraft(title="Execute controlled Run")],
        ),
    )


async def _wait_for_control_settlement(
    store: PostgresStore,
    task_id: str,
    expected_status: str,
) -> dict[str, Any]:
    """Return the final projection, including a stuck request for clear assertions."""

    latest: dict[str, Any] | None = None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.5
    while loop.time() < deadline:
        latest = store.get_task(WORKSPACE_ID, task_id)
        assert latest is not None
        if latest["status"] in {expected_status, "failed"}:
            return latest
        await asyncio.sleep(0.01)
    assert latest is not None
    return latest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "run_status",
    ["waiting_for_approval", "failed", "cancelled"],
)
@pytest.mark.parametrize(
    ("command_name", "expected_status"),
    [("pause", "paused"), ("cancel", "cancelled")],
)
async def test_control_request_wins_when_active_run_settles_non_completed(
    run_status: str,
    command_name: str,
    expected_status: str,
) -> None:
    store = create_test_store()
    runner = _ControlledTerminalRun(store, run_status)
    coordinator = TaskCoordinator(store, runner)
    service = TaskService(store, dispatch=coordinator.schedule)
    try:
        task = _create_task(store)
        task = service.command(
            WORKSPACE_ID,
            str(task["id"]),
            TaskCommand(
                command="start",
                idempotency_key=f"start-{command_name}-{run_status}",
                expected_revision=int(task["revision"]),
                expected_status="ready",
            ),
        )
        await asyncio.wait_for(runner.started.wait(), timeout=1)

        active = store.get_task(WORKSPACE_ID, str(task["id"]))
        assert active is not None
        requested = service.command(
            WORKSPACE_ID,
            str(task["id"]),
            TaskCommand(
                command=command_name,
                idempotency_key=f"{command_name}-{run_status}",
                expected_revision=int(active["revision"]),
                expected_status="running",
                message="Operator control must win at the Run boundary.",
            ),
        )
        assert requested["status"] == f"{command_name}_requested"

        runner.release.set()
        settled = await _wait_for_control_settlement(
            store,
            str(task["id"]),
            expected_status,
        )

        event_types = [
            event["type"]
            for event in store.list_task_events(WORKSPACE_ID, str(task["id"]))
        ]
        assert settled["status"] == expected_status, (
            f"active Run settled as {run_status}, but {command_name} left Task "
            f"in {settled['status']}; events={event_types}"
        )
        assert settled["status"] != "failed"
        assert event_types[-1] == f"task.{expected_status}"
    finally:
        runner.release.set()
        await coordinator.close()
        store.close()
