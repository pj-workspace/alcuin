from __future__ import annotations

import asyncio
from typing import Any

import pytest

from alcuin_api.tasks import TaskCoordinator, TaskService
from alcuin_core.tasks import TaskCreate, TaskStepDraft
from alcuin_storage import PostgresStore
from support import create_test_store


WORKSPACE_ID = "ws_demo"


def _create_task(store: PostgresStore) -> dict[str, Any]:
    agent = store.list_agents(WORKSPACE_ID)[0]
    thread = store.create_thread(
        WORKSPACE_ID,
        str(agent["id"]),
        "Task recovery boundary",
        {},
    )
    return TaskService(store).create(
        WORKSPACE_ID,
        str(thread["id"]),
        TaskCreate(
            goal="Produce one durable, evidence-backed result.",
            steps=[TaskStepDraft(title="Produce the result")],
        ),
    )


def _start_only_step(store: PostgresStore, task: dict[str, Any]) -> dict[str, Any]:
    task = store.start_task(
        WORKSPACE_ID,
        str(task["id"]),
        expected_revision=int(task["revision"]),
    )
    return store.start_task_step(
        WORKSPACE_ID,
        str(task["id"]),
        str(task["current_step_id"]),
        expected_revision=int(task["revision"]),
    )


async def _wait_for_task(
    store: PostgresStore,
    task_id: str,
    status: str,
) -> dict[str, Any]:
    async with asyncio.timeout(2):
        while True:
            task = store.get_task(WORKSPACE_ID, task_id)
            assert task is not None
            if task["status"] == status:
                return task
            await asyncio.sleep(0.01)


class _ForbiddenReplayRunner:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(
        self,
        _workspace_id: str,
        _task: dict[str, Any],
        _step: dict[str, Any],
        _attempt_id: str,
    ) -> dict[str, Any]:
        self.calls += 1
        raise AssertionError("recovery must reconcile the durable Run, not replay it")


@pytest.mark.asyncio
async def test_recovery_reconciles_completed_linked_run_without_new_run() -> None:
    store = create_test_store()
    runner = _ForbiddenReplayRunner()
    coordinator = TaskCoordinator(store, runner)
    try:
        task = _start_only_step(store, _create_task(store))
        step = task["plan"]["steps"][0]
        attempt = step["attempts"][-1]
        thread = store.get_thread(WORKSPACE_ID, str(task["thread_id"]))
        assert thread is not None
        run = store.create_run(
            WORKSPACE_ID,
            str(task["thread_id"]),
            str(thread["agent_version_id"]),
            "A Run completed immediately before process restart",
        )
        store.link_task_run(
            WORKSPACE_ID,
            str(task["id"]),
            str(step["id"]),
            str(attempt["id"]),
            str(run["id"]),
        )
        store.set_run_status(WORKSPACE_ID, str(run["id"]), "completed")

        recovered = coordinator.recover()

        assert len(recovered) == 1
        assert recovered[0]["id"] == task["id"]
        assert recovered[0]["previous_status"] == "running"
        assert recovered[0]["recovery_action"] == "reconcile_linked_run"
        completed = await _wait_for_task(store, str(task["id"]), "completed")
        runs = store.list_thread_runs(WORKSPACE_ID, str(task["thread_id"]))

        assert runner.calls == 0
        assert [item["id"] for item in runs] == [run["id"]]
        assert completed["plan"]["steps"][0]["attempts"][0]["run_id"] == run["id"]
        event_types = [
            event["type"]
            for event in store.list_task_events(WORKSPACE_ID, str(task["id"]))
        ]
        assert event_types[-3:] == [
            "task.attempt.completed",
            "task.step.completed",
            "task.completed",
        ]
    finally:
        await coordinator.close()
        store.close()


class _ContinuationRunner:
    def __init__(
        self,
        store: PostgresStore,
        *,
        expected_kind: str,
        expected_message: str,
    ) -> None:
        self.store = store
        self.expected_kind = expected_kind
        self.expected_message = expected_message
        self.calls: list[str] = []

    async def __call__(
        self,
        workspace_id: str,
        task: dict[str, Any],
        step: dict[str, Any],
        attempt_id: str,
    ) -> dict[str, Any]:
        self.calls.append(str(step["id"]))
        intervention = task["pending_intervention"]
        assert intervention["kind"] == self.expected_kind
        assert intervention["message"] == self.expected_message
        canonical = self.store.get_task(workspace_id, str(task["id"]))
        assert canonical is not None
        assert canonical["pending_intervention"] is None
        return {
            "id": f"run_continuation_{attempt_id}",
            "status": "completed",
            "output_message_id": None,
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["queue", "steer"])
async def test_pending_last_step_guidance_creates_and_consumes_a_continuation(
    kind: str,
) -> None:
    store = create_test_store()
    message = f"{kind.title()} one final verification before completion."
    runner = _ContinuationRunner(
        store,
        expected_kind=kind,
        expected_message=message,
    )
    coordinator = TaskCoordinator(store, runner)
    try:
        task = _start_only_step(store, _create_task(store))
        original_step_id = str(task["current_step_id"])
        task = store.complete_task_step(
            WORKSPACE_ID,
            str(task["id"]),
            original_step_id,
            expected_revision=int(task["revision"]),
            output={"summary": "The original final step reached its checkpoint."},
        )
        assert task["current_step_id"] is None
        task = store.intervene_task(
            WORKSPACE_ID,
            str(task["id"]),
            expected_revision=int(task["revision"]),
            kind=kind,
            content=message,
        )
        intervention_id = str(task["pending_intervention"]["id"])

        continued = store.complete_task(
            WORKSPACE_ID,
            str(task["id"]),
            expected_revision=int(task["revision"]),
            result={"summary": "Completion was requested before pending guidance."},
        )

        assert continued["status"] == "running"
        assert continued["pending_intervention"]["id"] == intervention_id
        assert len(continued["plan"]["steps"]) == 2
        continuation = continued["plan"]["steps"][-1]
        assert continuation["id"] == continued["current_step_id"]
        assert continuation["status"] == "pending"
        assert continuation["title"].startswith("Apply guidance:")

        coordinator.schedule(WORKSPACE_ID, str(task["id"]))
        completed = await _wait_for_task(store, str(task["id"]), "completed")
        interventions = store.list_task_interventions(
            WORKSPACE_ID,
            str(task["id"]),
        )

        assert runner.calls == [continuation["id"]]
        assert completed["pending_intervention"] is None
        assert [item["status"] for item in interventions] == ["applied"]
        assert completed["plan"]["steps"][-1]["status"] == "completed"
        relevant_events = [
            event
            for event in store.list_task_events(WORKSPACE_ID, str(task["id"]))
            if event["type"]
            in {
                "task.plan.updated",
                "task.intervention.applied",
                "task.completed",
            }
        ]
        assert [event["type"] for event in relevant_events[-3:]] == [
            "task.plan.updated",
            "task.intervention.applied",
            "task.completed",
        ]
        assert relevant_events[-2]["payload"]["intervention_id"] == intervention_id
    finally:
        await coordinator.close()
        store.close()


def test_interrupt_is_resolved_before_resume_and_new_queue_guidance() -> None:
    store = create_test_store()
    try:
        task = _start_only_step(store, _create_task(store))
        interrupted_attempt_id = task["plan"]["steps"][0]["attempts"][-1]["id"]
        interrupted = store.intervene_task(
            WORKSPACE_ID,
            str(task["id"]),
            expected_revision=int(task["revision"]),
            kind="interrupt",
            content="Pause now and preserve the durable boundary.",
        )
        interrupt_id = str(interrupted["pending_intervention"]["id"])
        assert interrupted["status"] == "pause_requested"

        paused = store.mark_task_paused(
            WORKSPACE_ID,
            str(task["id"]),
            expected_revision=int(interrupted["revision"]),
        )

        assert paused["status"] == "paused"
        assert paused["pending_intervention"] is None
        assert paused["plan"]["steps"][0]["status"] == "pending"
        assert paused["plan"]["steps"][0]["attempts"][-1]["status"] == "interrupted"
        assert paused["plan"]["steps"][0]["attempts"][-1]["id"] == (
            interrupted_attempt_id
        )

        resumed = store.resume_task(
            WORKSPACE_ID,
            str(task["id"]),
            expected_revision=int(paused["revision"]),
        )
        queued = store.intervene_task(
            WORKSPACE_ID,
            str(task["id"]),
            expected_revision=int(resumed["revision"]),
            kind="queue",
            content="Use the updated evidence after resuming.",
        )

        pending = store.list_task_interventions(
            WORKSPACE_ID,
            str(task["id"]),
            pending_only=True,
        )
        all_interventions = store.list_task_interventions(
            WORKSPACE_ID,
            str(task["id"]),
        )
        by_id = {item["id"]: item for item in all_interventions}
        assert queued["status"] == "running"
        assert queued["pending_intervention"]["kind"] == "queue"
        assert len(pending) == 1
        assert pending[0]["kind"] == "queue"
        assert by_id[interrupt_id]["status"] == "applied"
        assert by_id[interrupt_id]["applied_at"] is not None
        applied_interrupt_events = [
            event
            for event in store.list_task_events(WORKSPACE_ID, str(task["id"]))
            if event["type"] == "task.intervention.applied"
            and event["payload"]["intervention_id"] == interrupt_id
        ]
        assert len(applied_interrupt_events) == 1
    finally:
        store.close()
