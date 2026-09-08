"""Task model controls survive durable boundaries and use chat's catalog policy."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from alcuin_api.config import Settings
from alcuin_api.main import create_app
from alcuin_api.runtime import RuntimeRequest
from alcuin_api.tasks import RuntimeTaskStepRunner, TaskCoordinator, TaskService
from alcuin_core.contracts import ReasoningEffort
from alcuin_core.tasks import TaskCommand, TaskCreate, TaskStepDraft
from alcuin_storage import PostgresStore
from support import create_test_store, test_database_url as database_url


WORKSPACE_ID = "ws_demo"
HEADERS = {"X-Alcuin-Workspace": WORKSPACE_ID}
MODEL = "deepseek-v4-pro"


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        searxng_url="",
        qdrant_url="",
        dashscope_api_key="",
        deepseek_api_key="",
        openai_api_key="",
    )


def _checkpoint_profile(store: PostgresStore, task_id: str) -> dict[str, Any]:
    row = store._one(
        """SELECT state_json FROM task_checkpoints
        WHERE workspace_id = ? AND task_id = ? ORDER BY sequence DESC LIMIT 1""",
        (WORKSPACE_ID, task_id),
    )
    assert row is not None
    return json.loads(row["state_json"])["task"]


@pytest.mark.parametrize("effort", ["none", "low", "medium", "high"])
def test_task_api_persists_selected_profile_and_rejects_profile_plan_edits(
    effort: str,
) -> None:
    store = create_test_store()
    try:
        app = create_app(_settings(), store=store)
        with TestClient(app) as client:
            thread = client.post(
                "/v1/threads", headers=HEADERS, json={"agent_id": "agt_starter"}
            ).json()
            response = client.post(
                f"/v1/threads/{thread['id']}/tasks",
                headers=HEADERS,
                json={
                    "goal": "Create a concise brief",
                    "model_override": MODEL,
                    "reasoning_effort": effort,
                    "steps": [{"title": "Read the evidence"}],
                },
            )
            assert response.status_code == 201, response.text
            task = response.json()
            assert task["model_override"] == MODEL
            assert task["reasoning_effort"] == effort
            checkpoint_profile = _checkpoint_profile(store, task["id"])
            assert checkpoint_profile["model_override"] == MODEL
            assert checkpoint_profile["reasoning_effort"] == effort
            canonical = client.get(f"/v1/tasks/{task['id']}", headers=HEADERS).json()
            assert canonical["model_override"] == MODEL
            listed = client.get("/v1/tasks", headers=HEADERS).json()
            assert listed[0]["reasoning_effort"] == effort
            events = store.list_task_events(WORKSPACE_ID, task["id"])
            assert events[0]["payload"]["model_override"] == MODEL
            assert events[0]["payload"]["reasoning_effort"] == effort
            # A plan edit cannot silently change the profile chosen at creation.
            changed = client.patch(
                f"/v1/tasks/{task['id']}/plan",
                headers=HEADERS,
                json={
                    "expected_revision": task["revision"],
                    "steps": [{"title": "Changed plan"}],
                    "model_override": "deepseek-v4-flash",
                },
            )
            assert changed.status_code == 422
    finally:
        store.close()


@pytest.mark.parametrize(
    "controls",
    [
        {"model_override": "gpt-4.1-mini"},
        {"model_override": "unavailable-model"},
        {"model_override": ""},
        {"model_override": "deepseek-v4-pro\n"},
        {"reasoning_effort": "ultra"},
    ],
)
def test_task_api_rejects_invalid_profile_without_persisting_a_task(
    controls: dict,
) -> None:
    store = create_test_store()
    try:
        with TestClient(create_app(_settings(), store=store)) as client:
            thread = client.post(
                "/v1/threads", headers=HEADERS, json={"agent_id": "agt_starter"}
            ).json()
            response = client.post(
                f"/v1/threads/{thread['id']}/tasks",
                headers=HEADERS,
                json={"goal": "Create a brief", **controls},
            )
            assert response.status_code == 422, response.text
            assert store.list_tasks(WORKSPACE_ID) == []
    finally:
        store.close()


def test_task_rejects_unsupported_effort_in_the_selected_provider_catalog(
    monkeypatch,
) -> None:
    original_provider = Settings.provider

    def restricted_provider(settings: Settings, provider_id: str):
        provider = original_provider(settings, provider_id)
        return replace(
            provider,
            models=tuple(
                replace(model, reasoning_efforts=("none", "low"))
                if model.id == MODEL
                else model
                for model in provider.models
            ),
        )

    monkeypatch.setattr(Settings, "provider", restricted_provider)
    store = create_test_store()
    try:
        with TestClient(create_app(_settings(), store=store)) as client:
            thread = client.post(
                "/v1/threads", headers=HEADERS, json={"agent_id": "agt_starter"}
            ).json()
            response = client.post(
                f"/v1/threads/{thread['id']}/tasks",
                headers=HEADERS,
                json={
                    "goal": "Create a brief",
                    "model_override": MODEL,
                    "reasoning_effort": "high",
                },
            )
            assert response.status_code == 422
            assert (
                response.json()["detail"]
                == "reasoning_effort is unavailable for this model"
            )
            assert store.list_tasks(WORKSPACE_ID) == []
    finally:
        store.close()


class _CapturingRuntime:
    def __init__(self, store: PostgresStore, *, fail: bool = False) -> None:
        self.store = store
        self.requests: list[RuntimeRequest] = []
        self.fail = fail
        self.pause_task_id: str | None = None

    async def execute(self, request: RuntimeRequest) -> None:
        self.requests.append(request)
        self.store.set_run_status(
            WORKSPACE_ID, request.run_id, "failed" if self.fail else "completed"
        )
        if self.pause_task_id is not None:
            task = self.store.get_task(WORKSPACE_ID, self.pause_task_id)
            assert task is not None
            self.store.request_task_pause(
                WORKSPACE_ID, self.pause_task_id, expected_revision=task["revision"]
            )


async def _wait(store: PostgresStore, task_id: str, status: str) -> dict[str, Any]:
    async with asyncio.timeout(3):
        while True:
            task = store.get_task(WORKSPACE_ID, task_id)
            assert task is not None
            if task["status"] == status:
                return task
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["pause", "retry", "restart"])
async def test_selected_profile_survives_store_reopen_and_execution_boundaries(
    boundary: str,
) -> None:
    settings = _settings()
    store = create_test_store()
    runtime = _CapturingRuntime(store, fail=boundary == "retry")
    coordinator = TaskCoordinator(
        store, RuntimeTaskStepRunner(store, settings, runtime)
    )
    requests: list[RuntimeRequest] = []
    try:
        thread = store.create_thread(
            WORKSPACE_ID, "agt_starter", "Profile persistence", {}
        )
        service = TaskService(store, settings=settings, dispatch=coordinator.schedule)
        task = service.create(
            WORKSPACE_ID,
            thread["id"],
            TaskCreate(
                goal="Produce the final brief",
                model_override=MODEL,
                reasoning_effort="none",
                steps=[
                    TaskStepDraft(title="Read evidence"),
                    TaskStepDraft(title="Write brief"),
                ],
            ),
        )
        if boundary == "pause":
            runtime.pause_task_id = task["id"]
        if boundary == "restart":
            # Simulate a durable start accepted immediately before worker startup.
            task = store.start_task(
                WORKSPACE_ID, task["id"], expected_revision=task["revision"]
            )
        else:
            service.command(
                WORKSPACE_ID,
                task["id"],
                TaskCommand(
                    command="start",
                    idempotency_key="profile-start",
                    expected_revision=task["revision"],
                ),
            )
            task = await _wait(
                store,
                task["id"],
                "paused" if boundary == "pause" else "waiting_for_user",
            )
        await coordinator.close()
        requests.extend(runtime.requests)
        store.close()

        store = PostgresStore(database_url(), pool_max_size=4)
        runtime = _CapturingRuntime(store)
        coordinator = TaskCoordinator(
            store, RuntimeTaskStepRunner(store, settings, runtime)
        )
        service = TaskService(store, settings=settings, dispatch=coordinator.schedule)
        canonical = service.get(WORKSPACE_ID, task["id"])
        assert canonical["model_override"] == MODEL
        assert canonical["reasoning_effort"] == "none"
        if boundary == "restart":
            coordinator.recover()
            canonical = service.get(WORKSPACE_ID, task["id"])
            assert canonical["status"] == "paused"
        service.command(
            WORKSPACE_ID,
            task["id"],
            TaskCommand(
                command="retry" if boundary == "retry" else "resume",
                idempotency_key="profile-resume",
                expected_revision=canonical["revision"],
                step_id=canonical["plan"]["steps"][0]["id"]
                if boundary == "retry"
                else None,
            ),
        )
        completed = await _wait(store, task["id"], "completed")
        requests.extend(runtime.requests)
        assert len(requests) == (3 if boundary == "retry" else 2)
        assert all(request.effective_model == MODEL for request in requests)
        assert all(
            request.reasoning_effort is ReasoningEffort.NONE for request in requests
        )
        assert all(request.thinking is False for request in requests)
        assert _checkpoint_profile(store, completed["id"])["model_override"] == MODEL
    finally:
        await coordinator.close()
        store.close()


@pytest.mark.asyncio
async def test_runner_revalidates_catalog_before_creating_a_run(monkeypatch) -> None:
    settings = _settings()
    store = create_test_store()
    runtime = _CapturingRuntime(store)
    coordinator = TaskCoordinator(
        store, RuntimeTaskStepRunner(store, settings, runtime)
    )
    try:
        thread = store.create_thread(
            WORKSPACE_ID, "agt_starter", "Profile catalog change", {}
        )
        service = TaskService(store, settings=settings, dispatch=coordinator.schedule)
        task = service.create(
            WORKSPACE_ID,
            thread["id"],
            TaskCreate(
                goal="Create a brief",
                model_override=MODEL,
                steps=[TaskStepDraft(title="Write brief")],
            ),
        )
        original_provider = Settings.provider

        def without_selected_model(settings: Settings, provider_id: str):
            provider = original_provider(settings, provider_id)
            return replace(
                provider,
                models=tuple(model for model in provider.models if model.id != MODEL),
            )

        monkeypatch.setattr(Settings, "provider", without_selected_model)
        service.command(
            WORKSPACE_ID,
            task["id"],
            TaskCommand(
                command="start",
                idempotency_key="catalog-removed",
                expected_revision=task["revision"],
            ),
        )
        failed = await _wait(store, task["id"], "failed")
        assert "model_override is unavailable" in failed["error"]
        assert runtime.requests == []
        assert store.list_thread_runs(WORKSPACE_ID, thread["id"]) == []
    finally:
        await coordinator.close()
        store.close()
