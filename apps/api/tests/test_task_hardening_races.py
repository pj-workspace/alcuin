from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Any

import pytest
from alcuin_api.config import Settings
from alcuin_api.main import create_app
from alcuin_api.tasks import TaskCoordinator, TaskService
from alcuin_core.tasks import TaskCreate, TaskStepDraft
from alcuin_storage import PostgresStore, RepositoryConflict
from fastapi.testclient import TestClient
from support import create_test_store
from support import test_database_url as _test_database_url

WORKSPACE_ID = "ws_demo"
HEADERS = {"X-Alcuin-Workspace": WORKSPACE_ID}


def _settings() -> Settings:
    return Settings(
        searxng_url="",
        qdrant_url="",
        dashscope_api_key="",
        deepseek_api_key="",
        openai_api_key="",
    )


def _create_task(store: PostgresStore) -> dict[str, Any]:
    agent = store.list_agents(WORKSPACE_ID)[0]
    thread = store.create_thread(
        WORKSPACE_ID,
        str(agent["id"]),
        "Task hardening race",
        {},
    )
    return TaskService(store).create(
        WORKSPACE_ID,
        str(thread["id"]),
        TaskCreate(
            goal="Prove that one durable owner controls each Task boundary.",
            steps=[TaskStepDraft(title="Execute exactly once")],
        ),
    )


def _waiting_approval_fixture(store: PostgresStore) -> dict[str, Any]:
    task = _create_task(store)
    task = store.start_task(
        WORKSPACE_ID,
        str(task["id"]),
        expected_revision=int(task["revision"]),
    )
    step_id = str(task["current_step_id"])
    task = store.start_task_step(
        WORKSPACE_ID,
        str(task["id"]),
        step_id,
        expected_revision=int(task["revision"]),
    )
    attempt = task["plan"]["steps"][0]["attempts"][-1]
    thread = store.get_thread(WORKSPACE_ID, str(task["thread_id"]))
    assert thread is not None
    run = store.create_run(
        WORKSPACE_ID,
        str(task["thread_id"]),
        str(thread["agent_version_id"]),
        "Wait for a governed mutation approval",
    )
    store.link_task_run(
        WORKSPACE_ID,
        str(task["id"]),
        step_id,
        str(attempt["id"]),
        str(run["id"]),
    )
    approval = store.create_approval(
        WORKSPACE_ID,
        str(run["id"]),
        {
            "tool": "test.write",
            "call_id": "call_task_approval_race",
            "arguments": {"record_id": "REC-task-approval-race"},
        },
    )
    store.append_event(
        WORKSPACE_ID,
        str(run["id"]),
        "approval.required",
        {"approval_id": approval["id"], "tool": "test.write"},
    )
    store.set_run_status(WORKSPACE_ID, str(run["id"]), "waiting_for_approval")
    task = store.mark_task_waiting_for_approval(
        WORKSPACE_ID,
        str(task["id"]),
        step_id,
        str(approval["id"]),
        expected_revision=int(task["revision"]),
    )
    return {"task": task, "run": run, "approval": approval}


def _control_command(
    store: PostgresStore,
    fixture: dict[str, Any],
    command_name: str,
) -> dict[str, Any]:
    task = fixture["task"]
    return store.execute_task_command(
        WORKSPACE_ID,
        str(task["id"]),
        {
            "command": command_name,
            "idempotency_key": f"{command_name}-approval-race",
            "expected_revision": int(task["revision"]),
            "expected_status": "waiting_for_approval",
            "message": "Operator control raced the approval decision.",
        },
    )


@pytest.mark.parametrize("command_name", ["pause", "cancel"])
def test_task_control_winning_approval_race_prevents_tool_execution(
    monkeypatch: pytest.MonkeyPatch,
    command_name: str,
) -> None:
    setup_store = create_test_store()
    control_store = PostgresStore(_test_database_url(), pool_max_size=2)
    approval_store = PostgresStore(_test_database_url(), pool_max_size=2)
    control_holds_task = Event()
    release_control = Event()
    fixture = _waiting_approval_fixture(setup_store)
    original_require = control_store._require_task_status

    class _RecordingRuntime:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def resume_after_approval(
            self,
            workspace_id: str,
            run_id: str,
            approved: bool,
            request_payload: dict[str, Any],
        ) -> None:
            task = approval_store.get_task(WORKSPACE_ID, str(fixture["task"]["id"]))
            assert task is not None
            self.calls.append(str(task["status"]))

    runtime = _RecordingRuntime()
    app = create_app(_settings(), store=approval_store)
    app.state.runtime = runtime
    monkeypatch.setattr(app.state.task_coordinator, "schedule", lambda *_args: None)

    def hold_control_lock(
        task: dict[str, Any],
        command: str,
        allowed: set[str],
    ) -> None:
        original_require(task, command, allowed)
        if command.startswith("request "):
            control_holds_task.set()
            assert release_control.wait(timeout=2)

    monkeypatch.setattr(control_store, "_require_task_status", hold_control_lock)

    try:
        with (
            TestClient(app) as client,
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            control_future = executor.submit(
                _control_command,
                control_store,
                fixture,
                command_name,
            )
            assert control_holds_task.wait(timeout=1)
            decision_future = executor.submit(
                client.post,
                (
                    f"/v1/runs/{fixture['run']['id']}/approvals/"
                    f"{fixture['approval']['id']}"
                ),
                headers=HEADERS,
                json={
                    "decision": "approved",
                    "note": "Approve only if Task control has not won.",
                },
            )
            assert not decision_future.done()
            release_control.set()

            controlled = control_future.result(timeout=2)
            response = decision_future.result(timeout=2)

        canonical = setup_store.get_task(WORKSPACE_ID, str(fixture["task"]["id"]))
        approval = setup_store.get_approval(
            WORKSPACE_ID,
            str(fixture["approval"]["id"]),
        )
        assert canonical is not None
        assert approval is not None
        assert response.status_code == 409
        assert controlled["status"] == f"{command_name}_requested"
        assert canonical["status"] == f"{command_name}_requested"
        assert approval["status"] == "pending"
        assert runtime.calls == []
    finally:
        release_control.set()
        approval_store.close()
        control_store.close()
        setup_store.close()


@pytest.mark.parametrize("command_name", ["pause", "cancel"])
def test_task_approval_winning_control_race_returns_running_before_execution(
    monkeypatch: pytest.MonkeyPatch,
    command_name: str,
) -> None:
    setup_store = create_test_store()
    approval_store = PostgresStore(_test_database_url(), pool_max_size=2)
    control_store = PostgresStore(_test_database_url(), pool_max_size=2)
    approval_holds_task = Event()
    release_approval = Event()
    fixture = _waiting_approval_fixture(setup_store)
    original_require = approval_store._require_task_status

    def hold_approval_lock(
        task: dict[str, Any],
        command: str,
        allowed: set[str],
    ) -> None:
        original_require(task, command, allowed)
        if command == "decide approval for":
            approval_holds_task.set()
            assert release_approval.wait(timeout=2)

    monkeypatch.setattr(approval_store, "_require_task_status", hold_approval_lock)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            decision_future = executor.submit(
                approval_store.decide_task_approval,
                WORKSPACE_ID,
                str(fixture["approval"]["id"]),
                "approved",
                "Approval wins this durable boundary.",
            )
            assert approval_holds_task.wait(timeout=1)
            control_future = executor.submit(
                _control_command,
                control_store,
                fixture,
                command_name,
            )
            assert not control_future.done()
            release_approval.set()

            outcome = decision_future.result(timeout=2)
            assert outcome is not None
            assert outcome["approval"]["status"] == "approved"
            assert outcome["task"]["status"] == "running"
            with pytest.raises(RepositoryConflict):
                control_future.result(timeout=2)

        canonical = setup_store.get_task(WORKSPACE_ID, str(fixture["task"]["id"]))
        assert canonical is not None
        assert canonical["status"] == "running"
    finally:
        release_approval.set()
        control_store.close()
        approval_store.close()
        setup_store.close()


class _ExactlyOnceRun:
    def __init__(self, store: PostgresStore) -> None:
        self.store = store
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(
        self,
        workspace_id: str,
        task: dict[str, Any],
        step: dict[str, Any],
        attempt_id: str,
    ) -> dict[str, Any]:
        self.calls += 1
        thread = self.store.get_thread(workspace_id, str(task["thread_id"]))
        assert thread is not None
        run = self.store.create_run(
            workspace_id,
            str(task["thread_id"]),
            str(thread["agent_version_id"]),
            "Execute exactly once across competing coordinators",
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
        self.store.set_run_status(workspace_id, str(run["id"]), "completed")
        completed = self.store.get_run(workspace_id, str(run["id"]))
        assert completed is not None
        return completed


async def _wait_for_task(
    store: PostgresStore,
    task_id: str,
    predicate: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    async with asyncio.timeout(2):
        while True:
            task = store.get_task(WORKSPACE_ID, task_id)
            assert task is not None
            if predicate(task):
                return task
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_competing_task_coordinators_create_one_attempt_and_one_run() -> None:
    store = create_test_store()
    runner = _ExactlyOnceRun(store)
    coordinator_a = TaskCoordinator(store, runner)
    coordinator_b = TaskCoordinator(store, runner)
    try:
        task = _create_task(store)
        task = store.start_task(
            WORKSPACE_ID,
            str(task["id"]),
            expected_revision=int(task["revision"]),
        )

        coordinator_a.schedule(WORKSPACE_ID, str(task["id"]))
        coordinator_b.schedule(WORKSPACE_ID, str(task["id"]))
        await asyncio.wait_for(runner.started.wait(), timeout=1)

        active = store.get_task(WORKSPACE_ID, str(task["id"]))
        assert active is not None
        attempts = active["plan"]["steps"][0]["attempts"]
        runs = store.list_thread_runs(WORKSPACE_ID, str(task["thread_id"]))
        assert runner.calls == 1
        assert len(attempts) == 1
        assert len(runs) == 1
        assert attempts[0]["run_id"] == runs[0]["id"]

        runner.release.set()
        completed = await _wait_for_task(
            store,
            str(task["id"]),
            lambda current: current["status"] == "completed",
        )
        event_types = [
            event["type"]
            for event in store.list_task_events(WORKSPACE_ID, str(task["id"]))
        ]
        assert completed["status"] == "completed"
        assert len(completed["plan"]["steps"][0]["attempts"]) == 1
        assert runner.calls == 1
        assert "task.failed" not in event_types
    finally:
        runner.release.set()
        await coordinator_a.close()
        await coordinator_b.close()
        store.close()
