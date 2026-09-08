from __future__ import annotations

import asyncio
from collections.abc import Callable
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from alcuin_api.config import Settings
from alcuin_api.main import create_app
from alcuin_api.tasks import TaskCoordinator, TaskService
from alcuin_core.tasks import TaskCommand, TaskCreate, TaskStepDraft
from alcuin_storage import PostgresStore
from support import create_test_store


HEADERS = {"X-Alcuin-Workspace": "ws_demo"}
OTHER_HEADERS = {"X-Alcuin-Workspace": "ws_other"}


def _settings() -> Settings:
    return Settings(
        searxng_url="",
        qdrant_url="",
        dashscope_api_key="",
        deepseek_api_key="",
        openai_api_key="",
    )


def _create_thread(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/v1/threads",
        headers=HEADERS,
        json={"agent_id": "agt_starter", "context": {}},
    )
    assert response.status_code == 201
    return response.json()


def _create_api_task(
    client: TestClient,
    *,
    steps: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    thread = _create_thread(client)
    response = client.post(
        f"/v1/threads/{thread['id']}/tasks",
        headers=HEADERS,
        json={
            "goal": "Produce a verified operational brief.",
            "steps": steps or [],
        },
    )
    assert response.status_code == 201
    return response.json()


def _task_app(store: PostgresStore):
    app = create_app(_settings(), store=store)
    # API contract tests control dispatch explicitly so background execution cannot
    # race the assertion being made about one atomic command.
    app.state.task_service.set_dispatch(lambda _workspace_id, _task_id: None)
    return app


def test_task_api_enforces_workspace_ownership_on_every_resource_route() -> None:
    store = create_test_store()
    try:
        app = _task_app(store)
        with TestClient(app) as client:
            task = _create_api_task(
                client,
                steps=[{"title": "Collect evidence"}],
            )

            assert client.get("/v1/tasks", headers=OTHER_HEADERS).json() == []
            assert (
                client.get(f"/v1/tasks/{task['id']}", headers=OTHER_HEADERS).status_code
                == 404
            )
            assert (
                client.patch(
                    f"/v1/tasks/{task['id']}/plan",
                    headers=OTHER_HEADERS,
                    json={
                        "expected_revision": task["revision"],
                        "steps": [{"title": "Foreign rewrite"}],
                    },
                ).status_code
                == 404
            )
            assert (
                client.post(
                    f"/v1/tasks/{task['id']}/commands",
                    headers=OTHER_HEADERS,
                    json={
                        "command": "start",
                        "idempotency_key": "foreign-start-1",
                        "expected_revision": task["revision"],
                    },
                ).status_code
                == 404
            )
            assert (
                client.get(
                    f"/v1/tasks/{task['id']}/events", headers=OTHER_HEADERS
                ).status_code
                == 404
            )

            canonical = client.get(f"/v1/tasks/{task['id']}", headers=HEADERS)
            assert canonical.status_code == 200
            assert canonical.json()["workspace_id"] == "ws_demo"
    finally:
        store.close()


def test_task_plan_update_returns_conflict_for_stale_expected_revision() -> None:
    store = create_test_store()
    try:
        app = _task_app(store)
        with TestClient(app) as client:
            task = _create_api_task(client)
            first = client.patch(
                f"/v1/tasks/{task['id']}/plan",
                headers=HEADERS,
                json={
                    "expected_revision": task["revision"],
                    "goal": "Produce an evidence-backed brief.",
                    "steps": [
                        {"title": "Collect evidence"},
                        {"title": "Synthesize brief"},
                    ],
                },
            )
            assert first.status_code == 200

            stale = client.patch(
                f"/v1/tasks/{task['id']}/plan",
                headers=HEADERS,
                json={
                    "expected_revision": task["revision"],
                    "steps": [{"title": "Overwrite with stale state"}],
                },
            )
            assert stale.status_code == 409
            assert stale.json()["detail"]["code"] == "task_state_conflict"

            canonical = client.get(f"/v1/tasks/{task['id']}", headers=HEADERS).json()
            assert canonical["revision"] == first.json()["revision"]
            assert [step["title"] for step in canonical["plan"]["steps"]] == [
                "Collect evidence",
                "Synthesize brief",
            ]
    finally:
        store.close()


def test_start_command_replay_is_idempotent_and_dispatches_once() -> None:
    store = create_test_store()
    try:
        app = _task_app(store)
        dispatches: list[tuple[str, str]] = []
        app.state.task_service.set_dispatch(
            lambda workspace_id, task_id: dispatches.append((workspace_id, task_id))
        )
        with TestClient(app) as client:
            task = _create_api_task(
                client,
                steps=[{"title": "Collect evidence"}],
            )
            command = {
                "command": "start",
                "idempotency_key": "start-task-1",
                "expected_revision": task["revision"],
                "expected_status": "ready",
            }
            first = client.post(
                f"/v1/tasks/{task['id']}/commands",
                headers=HEADERS,
                json=command,
            )
            replay = client.post(
                f"/v1/tasks/{task['id']}/commands",
                headers=HEADERS,
                json=command,
            )

            assert first.status_code == 200
            assert replay.status_code == 200
            assert replay.json() == first.json()
            assert dispatches == [("ws_demo", task["id"])]
            events = store.list_task_events("ws_demo", task["id"])
            assert [event["type"] for event in events].count("task.started") == 1
            assert (
                store.get_task_command("ws_demo", task["id"], "start-task-1")
                is not None
            )
    finally:
        store.close()


class _CompletedStepRunner:
    def __init__(self, *, block_first: bool = False) -> None:
        self.calls: list[str] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not block_first:
            self.release.set()

    async def __call__(
        self,
        workspace_id: str,
        task: dict[str, Any],
        step: dict[str, Any],
        attempt_id: str,
    ) -> dict[str, Any]:
        assert workspace_id == task["workspace_id"]
        self.calls.append(str(step["title"]))
        self.started.set()
        await self.release.wait()
        return {
            "id": f"run_test_{attempt_id}",
            "status": "completed",
            "output_message_id": None,
        }


def _create_service_task(
    store: PostgresStore, *, step_count: int = 2
) -> dict[str, Any]:
    agent = store.list_agents("ws_demo")[0]
    thread = store.create_thread("ws_demo", agent["id"], "Task test", {})
    titles = ["Collect evidence", "Synthesize brief"][:step_count]
    return TaskService(store).create(
        "ws_demo",
        thread["id"],
        TaskCreate(
            goal="Produce a verified operational brief.",
            steps=[TaskStepDraft(title=title) for title in titles],
        ),
    )


async def _wait_for_task(
    store: PostgresStore,
    task_id: str,
    predicate: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    async with asyncio.timeout(2):
        while True:
            task = store.get_task("ws_demo", task_id)
            assert task is not None
            if predicate(task):
                return task
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command_name", "terminal_status"),
    [("pause", "paused"), ("cancel", "cancelled")],
)
async def test_pause_and_cancel_are_acknowledged_after_the_active_step_safe_boundary(
    command_name: str,
    terminal_status: str,
) -> None:
    store = create_test_store()
    runner = _CompletedStepRunner(block_first=True)
    coordinator = TaskCoordinator(store, runner)
    service = TaskService(store, dispatch=coordinator.schedule)
    try:
        task = _create_service_task(store)
        task = service.command(
            "ws_demo",
            task["id"],
            TaskCommand(
                command="start",
                idempotency_key=f"start-for-{command_name}",
                expected_revision=task["revision"],
            ),
        )
        await asyncio.wait_for(runner.started.wait(), timeout=1)

        active = store.get_task("ws_demo", task["id"])
        assert active is not None
        requested = service.command(
            "ws_demo",
            task["id"],
            TaskCommand(
                command=command_name,
                idempotency_key=f"{command_name}-at-boundary",
                expected_revision=active["revision"],
                expected_status="running",
                message="Stop after the current durable step.",
            ),
        )
        assert requested["status"] == f"{command_name}_requested"
        assert runner.calls == ["Collect evidence"]

        runner.release.set()
        settled = await _wait_for_task(
            store,
            task["id"],
            lambda current: current["status"] == terminal_status,
        )
        assert settled["plan"]["steps"][0]["status"] == "completed"
        assert runner.calls == ["Collect evidence"]
        if command_name == "pause":
            assert settled["plan"]["steps"][1]["status"] == "pending"
            assert settled["current_step_id"] == settled["plan"]["steps"][1]["id"]
        else:
            assert settled["plan"]["steps"][1]["status"] == "cancelled"
    finally:
        runner.release.set()
        await coordinator.close()
        store.close()


@pytest.mark.asyncio
async def test_recovery_pauses_at_checkpoint_then_resume_runs_only_remaining_steps() -> (
    None
):
    store = create_test_store()
    runner = _CompletedStepRunner()
    coordinator = TaskCoordinator(store, runner)
    service = TaskService(store, dispatch=coordinator.schedule)
    try:
        task = _create_service_task(store)
        task = store.start_task(
            "ws_demo", task["id"], expected_revision=task["revision"]
        )
        first_step = task["plan"]["steps"][0]
        task = store.start_task_step(
            "ws_demo",
            task["id"],
            first_step["id"],
            expected_revision=task["revision"],
        )
        task = store.complete_task_step(
            "ws_demo",
            task["id"],
            first_step["id"],
            expected_revision=task["revision"],
            output={"summary": "First step is durable"},
        )
        first_attempt_id = task["plan"]["steps"][0]["attempts"][0]["id"]
        checkpoint_sequence = task["latest_checkpoint"]["sequence"]

        recovered = coordinator.recover()
        assert recovered[0]["previous_status"] == "running"
        paused = store.get_task("ws_demo", task["id"])
        assert paused is not None
        assert paused["status"] == "paused"
        recovery_event = store.list_task_events("ws_demo", task["id"])[-1]
        assert recovery_event["type"] == "task.paused"
        assert recovery_event["payload"]["reason"] == "runtime_restarted"
        assert runner.calls == []

        resumed = service.command(
            "ws_demo",
            task["id"],
            TaskCommand(
                command="resume",
                idempotency_key="resume-after-restart",
                expected_revision=paused["revision"],
                expected_status="paused",
            ),
        )
        assert resumed["status"] == "running"
        completed = await _wait_for_task(
            store,
            task["id"],
            lambda current: current["status"] == "completed",
        )

        assert runner.calls == ["Synthesize brief"]
        assert [step["status"] for step in completed["plan"]["steps"]] == [
            "completed",
            "completed",
        ]
        assert completed["plan"]["steps"][0]["attempts"][0]["id"] == first_attempt_id
        assert len(completed["plan"]["steps"][0]["attempts"]) == 1
        assert completed["latest_checkpoint"]["sequence"] > checkpoint_sequence
        assert completed["result"]["completed_step_ids"] == [
            step["id"] for step in completed["plan"]["steps"]
        ]
    finally:
        await coordinator.close()
        store.close()


def test_approved_linked_run_resumes_and_reconciles_the_waiting_task() -> None:
    store = create_test_store()
    coordinator: TaskCoordinator | None = None
    try:
        task = _create_service_task(store, step_count=1)
        task = store.start_task(
            "ws_demo", task["id"], expected_revision=task["revision"]
        )
        step_id = task["current_step_id"]
        task = store.start_task_step(
            "ws_demo",
            task["id"],
            step_id,
            expected_revision=task["revision"],
        )
        attempt = task["plan"]["steps"][0]["attempts"][-1]
        thread = store.get_thread("ws_demo", task["thread_id"])
        assert thread is not None
        run = store.create_run(
            "ws_demo",
            task["thread_id"],
            thread["agent_version_id"],
            "Execute the approved Task step",
        )
        store.link_task_run(
            "ws_demo",
            task["id"],
            step_id,
            attempt["id"],
            run["id"],
        )
        approval = store.create_approval(
            "ws_demo",
            run["id"],
            {
                "tool": "test.write",
                "call_id": "call_approval_test",
                "arguments": {"record_id": "REC-1"},
            },
        )
        store.append_event(
            "ws_demo",
            run["id"],
            "approval.required",
            {"approval_id": approval["id"], "tool": "test.write"},
        )
        store.set_run_status("ws_demo", run["id"], "waiting_for_approval")
        task = store.mark_task_waiting_for_approval(
            "ws_demo",
            task["id"],
            step_id,
            approval["id"],
            expected_revision=task["revision"],
        )

        class _ApprovalRuntime:
            async def resume_after_approval(
                self,
                workspace_id: str,
                run_id: str,
                approved: bool,
                request_payload: dict[str, Any],
            ) -> None:
                assert workspace_id == "ws_demo"
                assert approved is True
                assert request_payload["tool"] == "test.write"
                store.set_run_status(workspace_id, run_id, "completed")

        async def unexpected_step_runner(
            _workspace_id: str,
            _task: dict[str, Any],
            _step: dict[str, Any],
            _attempt_id: str,
        ) -> dict[str, Any]:
            raise AssertionError("linked terminal Run must be reconciled, not replayed")

        app = create_app(_settings(), store=store)
        coordinator = TaskCoordinator(store, unexpected_step_runner)
        app.state.runtime = _ApprovalRuntime()
        app.state.task_coordinator = coordinator
        with TestClient(app) as client:
            response = client.post(
                f"/v1/runs/{run['id']}/approvals/{approval['id']}",
                headers=HEADERS,
                json={"decision": "approved", "note": "Verified by operator"},
            )
            assert response.status_code == 200
            assert response.json()["status"] == "approved"

            deadline = time.monotonic() + 2
            completed = store.get_task("ws_demo", task["id"])
            while completed and completed["status"] != "completed":
                assert time.monotonic() < deadline
                time.sleep(0.01)
                completed = store.get_task("ws_demo", task["id"])

            assert completed is not None
            assert completed["status"] == "completed"
            assert completed["plan"]["steps"][0]["status"] == "completed"
            assert completed["plan"]["steps"][0]["attempts"][0]["run_id"] == run["id"]
            assert [
                event["type"]
                for event in store.list_task_events("ws_demo", task["id"])
                if event["type"]
                in {"task.resumed", "task.step.completed", "task.completed"}
            ] == ["task.resumed", "task.step.completed", "task.completed"]
    finally:
        if coordinator is not None:
            # Closing is idempotent; this also protects failures before TestClient enters.
            asyncio.run(coordinator.close())
        store.close()
