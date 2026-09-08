from __future__ import annotations

import threading
from typing import Any

import pytest
from alcuin_api.config import Settings
from alcuin_api.main import create_app
from alcuin_api.tasks import TaskService
from alcuin_core.tasks import TaskCreate, TaskStepDraft
from alcuin_storage import PostgresStore
from fastapi.testclient import TestClient
from support import create_test_store

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


def _task_ready_for_terminal_transition(store: PostgresStore) -> dict[str, Any]:
    agent = store.list_agents(WORKSPACE_ID)[0]
    thread = store.create_thread(
        WORKSPACE_ID,
        agent["id"],
        "Task SSE terminal ordering",
        {},
    )
    task = TaskService(store).create(
        WORKSPACE_ID,
        thread["id"],
        TaskCreate(
            goal="Verify terminal Task event ordering.",
            steps=[TaskStepDraft(title="Finish the durable step")],
        ),
    )
    task = store.start_task(
        WORKSPACE_ID,
        task["id"],
        expected_revision=task["revision"],
    )
    step_id = task["current_step_id"]
    task = store.start_task_step(
        WORKSPACE_ID,
        task["id"],
        step_id,
        expected_revision=task["revision"],
    )
    return store.complete_task_step(
        WORKSPACE_ID,
        task["id"],
        step_id,
        expected_revision=task["revision"],
        output={"summary": "The durable step completed."},
    )


def test_terminal_task_event_is_emitted_before_done_when_committed_after_empty_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = create_test_store()
    terminal_committed = threading.Event()
    first_empty_poll = threading.Event()
    completion_result: dict[str, Any] = {}
    completion_error: list[Exception] = []
    worker: threading.Thread | None = None
    try:
        app = create_app(_settings(), store=store)
        app.state.task_service.set_dispatch(lambda _workspace_id, _task_id: None)

        with TestClient(app) as client:
            # Create the running Task after application startup so restart recovery
            # cannot legitimately pause it before the race is exercised.
            task = _task_ready_for_terminal_transition(store)
            existing_events = store.list_task_events(
                WORKSPACE_ID,
                task["id"],
                after=0,
                limit=500,
            )
            cursor = int(existing_events[-1]["sequence"])
            original_list_task_events = store.list_task_events
            intercept_first_poll = True

            def list_task_events_with_terminal_race(
                workspace_id: str,
                task_id: str,
                *,
                after: int = 0,
                limit: int = 500,
            ) -> list[dict[str, Any]]:
                nonlocal intercept_first_poll
                events = original_list_task_events(
                    workspace_id,
                    task_id,
                    after=after,
                    limit=limit,
                )
                if (
                    intercept_first_poll
                    and workspace_id == WORKSPACE_ID
                    and task_id == task["id"]
                    and after == cursor
                ):
                    intercept_first_poll = False
                    assert events == []
                    first_empty_poll.set()
                    if not terminal_committed.wait(timeout=2):
                        raise AssertionError("terminal transition was not released")
                return events

            monkeypatch.setattr(
                store,
                "list_task_events",
                list_task_events_with_terminal_race,
            )

            def commit_terminal_transition() -> None:
                try:
                    if not first_empty_poll.wait(timeout=2):
                        raise AssertionError(
                            "SSE route did not reach its first empty poll"
                        )
                    completion_result["task"] = store.complete_task(
                        WORKSPACE_ID,
                        task["id"],
                        expected_revision=task["revision"],
                        result={
                            "summary": (
                                "Task completed during the empty-poll race window."
                            )
                        },
                    )
                except Exception as exc:  # noqa: BLE001 - re-raised by the test thread
                    completion_error.append(exc)
                finally:
                    terminal_committed.set()

            worker = threading.Thread(target=commit_terminal_transition, daemon=True)
            worker.start()
            response = client.get(
                f"/v1/tasks/{task['id']}/events?after={cursor}",
                headers=HEADERS,
            )
            worker.join(timeout=2)
            assert not worker.is_alive(), "terminal transition did not finish"

        assert completion_error == []
        assert completion_result["task"]["status"] == "completed"
        assert response.status_code == 200
        body = response.text
        assert "event: task.completed" in body
        assert body.index("event: task.completed") < body.index("data: [DONE]")
    finally:
        terminal_committed.set()
        if worker is not None:
            worker.join(timeout=2)
        store.close()
