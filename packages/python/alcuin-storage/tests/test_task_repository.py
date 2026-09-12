from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from urllib.parse import urlsplit

import psycopg
import pytest

from alcuin_core.tasks import Task, TaskEvent
from alcuin_storage import (
    PostgresStore,
    RepositoryConflict,
    TaskRepository,
    TaskRevisionConflict,
    TaskTransitionConflict,
)


DATABASE_URL = os.environ.get("ALCUIN_TEST_POSTGRES_URL")


def require_test_database(url: str | None) -> str | None:
    if url is None:
        return None
    if not urlsplit(url).path.removeprefix("/").endswith("_test"):
        raise RuntimeError(
            "Refusing to reset a PostgreSQL database whose name does not end in '_test'"
        )
    return url


DATABASE_URL = require_test_database(DATABASE_URL)
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="ALCUIN_TEST_POSTGRES_URL is required for PostgreSQL Task tests",
)


def reset_database() -> None:
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute("TRUNCATE TABLE workspaces CASCADE")


@pytest.fixture
def store() -> PostgresStore:
    assert DATABASE_URL is not None
    reset_database()
    repository = PostgresStore(DATABASE_URL, pool_max_size=4)
    try:
        yield repository
    finally:
        repository.close()


def create_task(store: PostgresStore, *, title: str = "Risk review") -> dict:
    agent = store.list_agents("ws_demo")[0]
    thread = store.create_thread("ws_demo", agent["id"], title, {})
    return store.create_task(
        "ws_demo",
        thread["id"],
        "Assess the supplied operational evidence and produce a verified brief.",
        title=title,
        metadata={"source": "test"},
    )


def add_plan(store: PostgresStore, task: dict, count: int = 2) -> dict:
    return store.replace_task_plan(
        "ws_demo",
        task["id"],
        expected_revision=task["revision"],
        goal="Produce an evidence-backed operational brief.",
        steps=[
            {
                "key": f"step-{index + 1}",
                "title": title,
                "description": f"Execute {title.lower()}.",
                "input": {"index": index},
            }
            for index, title in enumerate(
                ["Collect evidence", "Synthesize brief"][:count]
            )
        ],
    )


def test_postgres_adapter_satisfies_task_port_and_scopes_projection(
    store: PostgresStore,
) -> None:
    assert isinstance(store, TaskRepository)
    created = create_task(store)
    assert created["status"] == "draft"
    assert created["revision"] == 1
    assert created["plan"]["steps"] == []
    assert created["latest_checkpoint"]["task_revision"] == 1
    assert created["pending_intervention"] is None
    Task.model_validate(created)
    assert store.get_task("ws_other", created["id"]) is None
    assert store.list_tasks("ws_other") == []
    assert store.list_task_events("ws_other", created["id"]) == []

    planned = add_plan(store, created)
    assert planned["goal"] == "Produce an evidence-backed operational brief."
    assert planned["status"] == "ready"
    assert planned["plan"]["task_id"] == planned["id"]
    assert planned["plan"]["updated_at"]
    assert [step["ordinal"] for step in planned["plan"]["steps"]] == [0, 1]
    assert all(step["attempts"] == [] for step in planned["plan"]["steps"])
    Task.model_validate(planned)
    assert store.list_tasks("ws_demo")[0]["plan"]["id"] == planned["plan"]["id"]

    events = store.list_task_events("ws_demo", created["id"])
    assert [event["type"] for event in events] == [
        "task.created",
        "task.plan.updated",
    ]
    assert [event["sequence"] for event in events] == [1, 2]
    assert all(event["workspace_id"] == "ws_demo" for event in events)
    for event in events:
        TaskEvent.model_validate(event)


def test_plan_events_preserve_complete_text_snapshots_across_edits(
    store: PostgresStore,
) -> None:
    created = create_task(store)
    first = store.replace_task_plan(
        "ws_demo",
        created["id"],
        expected_revision=created["revision"],
        goal="  Review the original evidence  ",
        steps=[
            {"title": "Read sources", "description": "Compare source A with source B."}
        ],
    )
    first_events = store.list_task_events("ws_demo", created["id"])
    second = store.replace_task_plan(
        "ws_demo",
        created["id"],
        expected_revision=first["revision"],
        goal="Produce the revised report",
        steps=[
            {"title": "Verify sources", "description": "Check source C before writing."}
        ],
    )
    # A description-only edit must still record the canonical, unchanged goal.
    store.replace_task_plan(
        "ws_demo",
        created["id"],
        expected_revision=second["revision"],
        steps=[
            {"title": "Verify sources", "description": "Check source C and its date."}
        ],
    )
    events = store.list_task_events("ws_demo", created["id"])
    assert events[:2] == first_events
    snapshots = [
        {
            "goal": event["payload"]["goal"],
            "steps": [
                {key: step[key] for key in ("title", "description", "position")}
                for step in event["payload"]["steps"]
            ],
        }
        for event in events
        if event["type"] in {"task.created", "task.plan.updated"}
    ]
    assert snapshots == [
        {"goal": created["goal"], "steps": []},
        {
            "goal": "Review the original evidence",
            "steps": [
                {
                    "title": "Read sources",
                    "description": "Compare source A with source B.",
                    "position": 0,
                }
            ],
        },
        {
            "goal": "Produce the revised report",
            "steps": [
                {
                    "title": "Verify sources",
                    "description": "Check source C before writing.",
                    "position": 0,
                }
            ],
        },
        {
            "goal": "Produce the revised report",
            "steps": [
                {
                    "title": "Verify sources",
                    "description": "Check source C and its date.",
                    "position": 0,
                }
            ],
        },
    ]
    assert [event["payload"]["generation"] for event in events] == [1, 2, 3, 4]
    assert store.list_task_events("ws_other", created["id"]) == []
    for event in events:
        TaskEvent.model_validate(event)


def test_task_revision_cas_and_transition_guards_are_canonical(
    store: PostgresStore,
) -> None:
    created = create_task(store)
    planned = add_plan(store, created)

    with pytest.raises(TaskRevisionConflict) as stale:
        store.start_task(
            "ws_demo",
            planned["id"],
            expected_revision=created["revision"],
        )
    assert stale.value.current_revision == planned["revision"]
    assert stale.value.current_status == "ready"

    started = store.start_task(
        "ws_demo",
        planned["id"],
        expected_revision=planned["revision"],
    )
    with pytest.raises(TaskTransitionConflict):
        store.start_task(
            "ws_demo",
            started["id"],
            expected_revision=started["revision"],
        )


def test_empty_plan_stays_non_runnable_and_workspace_scoped(
    store: PostgresStore,
) -> None:
    task = create_task(store)
    task = store.replace_task_plan(
        "ws_demo",
        task["id"],
        expected_revision=task["revision"],
        steps=[],
    )
    assert task["status"] == "planning"
    assert task["current_step_id"] is None
    assert task["plan"]["steps"] == []
    assert store.list_task_dispatches("ws_demo", status="blocked")[0][
        "task_id"
    ] == task["id"]
    Task.model_validate(task)
    with pytest.raises(TaskTransitionConflict):
        store.start_task(
            "ws_demo",
            task["id"],
            expected_revision=task["revision"],
        )


def test_sequential_steps_runs_attempts_and_completion_are_durable(
    store: PostgresStore,
) -> None:
    task = add_plan(store, create_task(store))
    task = store.start_task("ws_demo", task["id"], expected_revision=task["revision"])
    first_step = task["plan"]["steps"][0]
    task = store.start_task_step(
        "ws_demo",
        task["id"],
        first_step["id"],
        expected_revision=task["revision"],
        input={"query": "current evidence"},
    )
    attempt = task["plan"]["steps"][0]["attempts"][0]
    assert attempt["number"] == 1

    thread_id = task["thread_id"]
    thread = store.get_thread("ws_demo", thread_id)
    assert thread is not None
    run = store.create_run(
        "ws_demo",
        thread_id,
        thread["agent_version_id"],
        "Collect evidence",
    )
    link = store.link_task_run(
        "ws_demo",
        task["id"],
        first_step["id"],
        attempt["id"],
        run["id"],
    )
    assert store.get_task_run_link("ws_demo", run["id"]) == link
    assert store.get_task_run_link("ws_other", run["id"]) is None

    task = store.complete_task_step(
        "ws_demo",
        task["id"],
        first_step["id"],
        expected_revision=task["revision"],
        output={"summary": "Evidence collected"},
        evidence=[{"uri": "https://example.test/source"}],
    )
    assert task["plan"]["steps"][0]["status"] == "completed"
    assert task["plan"]["steps"][0]["attempts"][0]["run_id"] == run["id"]
    second_step = task["plan"]["steps"][1]
    assert task["current_step_id"] == second_step["id"]
    first_types = [
        event["type"] for event in store.list_task_events("ws_demo", task["id"])
    ]
    assert first_types[-4:] == [
        "task.step.started",
        "task.attempt.started",
        "task.attempt.completed",
        "task.step.completed",
    ]

    task = store.start_task_step(
        "ws_demo",
        task["id"],
        second_step["id"],
        expected_revision=task["revision"],
    )
    task = store.complete_task_step(
        "ws_demo",
        task["id"],
        second_step["id"],
        expected_revision=task["revision"],
        output={"artifact_id": "art_test"},
    )
    assert task["current_step_id"] is None
    with pytest.raises(RepositoryConflict, match="non-empty result"):
        store.complete_task(
            "ws_demo",
            task["id"],
            expected_revision=task["revision"],
            result={},
        )
    task = store.complete_task(
        "ws_demo",
        task["id"],
        expected_revision=task["revision"],
        result={"artifact_id": "art_test"},
    )
    assert task["status"] == "completed"
    assert task["result"] == {"artifact_id": "art_test"}
    assert task["completed_at"]
    Task.model_validate(task)
    for event in store.list_task_events("ws_demo", task["id"]):
        TaskEvent.model_validate(event)
    assert store.list_task_dispatches("ws_demo", status="terminal")[0][
        "task_id"
    ] == task["id"]


def test_step_failure_waits_for_user_and_retry_creates_a_new_attempt(
    store: PostgresStore,
) -> None:
    task = add_plan(store, create_task(store), count=1)
    task = store.start_task("ws_demo", task["id"], expected_revision=task["revision"])
    step_id = task["current_step_id"]
    task = store.start_task_step(
        "ws_demo",
        task["id"],
        step_id,
        expected_revision=task["revision"],
    )
    task = store.fail_task_step(
        "ws_demo",
        task["id"],
        step_id,
        expected_revision=task["revision"],
        error={"code": "provider_timeout", "message": "Timed out"},
    )
    assert task["status"] == "waiting_for_user"
    assert task["completed_at"] is None
    assert task["plan"]["steps"][0]["status"] == "failed"
    Task.model_validate(task)
    assert [
        event["type"] for event in store.list_task_events("ws_demo", task["id"])
    ][-2:] == ["task.attempt.failed", "task.step.failed"]

    task = store.retry_task_step(
        "ws_demo",
        task["id"],
        step_id,
        expected_revision=task["revision"],
    )
    assert task["status"] == "running"
    assert task["plan"]["steps"][0]["status"] == "pending"
    task = store.start_task_step(
        "ws_demo",
        task["id"],
        step_id,
        expected_revision=task["revision"],
    )
    assert [attempt["number"] for attempt in task["plan"]["steps"][0]["attempts"]] == [
        1,
        2,
    ]


def test_pause_request_survives_step_safe_boundary_and_restart_recovery(
    store: PostgresStore,
) -> None:
    task = add_plan(store, create_task(store))
    task = store.start_task("ws_demo", task["id"], expected_revision=task["revision"])
    step_id = task["current_step_id"]
    task = store.start_task_step(
        "ws_demo",
        task["id"],
        step_id,
        expected_revision=task["revision"],
    )
    task = store.request_task_pause(
        "ws_demo",
        task["id"],
        expected_revision=task["revision"],
        reason="User requested a checkpoint",
    )
    task = store.complete_task_step(
        "ws_demo",
        task["id"],
        step_id,
        expected_revision=task["revision"],
        output={"safe_boundary": True},
    )
    assert task["status"] == "pause_requested"
    next_step_id = task["current_step_id"]
    task = store.mark_task_paused(
        "ws_demo",
        task["id"],
        expected_revision=task["revision"],
    )
    assert task["status"] == "paused"
    assert task["current_step_id"] == next_step_id
    task = store.resume_task(
        "ws_demo",
        task["id"],
        expected_revision=task["revision"],
    )
    assert task["status"] == "running"

    recovered = store.recover_nonterminal_tasks()
    assert recovered == [
        {
            "workspace_id": "ws_demo",
            "task_id": task["id"],
            "previous_status": "running",
            "status": "paused",
            "revision": task["revision"] + 2,
            "event_id": recovered[0]["event_id"],
        }
    ]
    persisted = store.get_task("ws_demo", task["id"])
    assert persisted is not None
    assert persisted["status"] == "paused"
    Task.model_validate(persisted)


def test_intervention_suspend_cancel_and_idempotency_records_are_scoped(
    store: PostgresStore,
) -> None:
    task = add_plan(store, create_task(store), count=1)
    task = store.start_task("ws_demo", task["id"], expected_revision=task["revision"])
    step_id = task["current_step_id"]
    task = store.start_task_step(
        "ws_demo",
        task["id"],
        step_id,
        expected_revision=task["revision"],
    )
    task = store.mark_task_waiting_for_approval(
        "ws_demo",
        task["id"],
        step_id,
        "apr_test",
        expected_revision=task["revision"],
    )
    assert task["status"] == "waiting_for_approval"
    task = store.resume_task(
        "ws_demo",
        task["id"],
        expected_revision=task["revision"],
    )
    with pytest.raises(ValueError, match="40000"):
        store.intervene_task(
            "ws_demo",
            task["id"],
            expected_revision=task["revision"],
            kind="queue",
            content="x" * 40_001,
        )
    long_message = "x" * 20_001
    task = store.intervene_task(
        "ws_demo",
        task["id"],
        expected_revision=task["revision"],
        kind="queue",
        content=long_message,
    )
    pending = task["pending_intervention"]
    assert pending["kind"] == "queue"
    assert pending["message"] == long_message
    assert len(store.list_task_interventions("ws_demo", task["id"], pending_only=True)) == 1
    task = store.apply_task_intervention(
        "ws_demo",
        task["id"],
        pending["id"],
        expected_revision=task["revision"],
    )
    assert task["pending_intervention"] is None

    task = store.execute_task_command(
        "ws_demo",
        task["id"],
        {
            "command": "cancel",
            "idempotency_key": "cancel-1",
            "expected_revision": task["revision"],
            "expected_status": "running",
        },
    )
    assert store.get_task_command("ws_other", task["id"], "cancel-1") is None
    task = store.mark_task_cancelled(
        "ws_demo",
        task["id"],
        expected_revision=task["revision"],
    )
    assert task["status"] == "cancelled"
    assert task["plan"]["steps"][0]["status"] == "cancelled"
    Task.model_validate(task)


def test_concurrent_task_commands_have_one_cas_winner(store: PostgresStore) -> None:
    task = add_plan(store, create_task(store))

    def start_once() -> str:
        try:
            return store.start_task(
                "ws_demo",
                task["id"],
                expected_revision=task["revision"],
            )["status"]
        except TaskRevisionConflict:
            return "stale"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: start_once(), range(2)))
    assert sorted(results) == ["running", "stale"]
    assert [event["type"] for event in store.list_task_events("ws_demo", task["id"])].count(
        "task.started"
    ) == 1


def test_execute_task_command_atomically_replays_the_canonical_result(
    store: PostgresStore,
) -> None:
    task = add_plan(store, create_task(store))
    command = {
        "command": "start",
        "idempotency_key": f"start:{task['id']}:1",
        "expected_revision": task["revision"],
        "expected_status": "ready",
        "step_id": None,
        "message": None,
    }
    with ThreadPoolExecutor(max_workers=2) as executor:
        started, replay = list(
            executor.map(
                lambda _index: store.execute_task_command(
                    "ws_demo", task["id"], command
                ),
                range(2),
            )
        )

    assert replay == started
    assert started["status"] == "running"
    assert [event["type"] for event in store.list_task_events("ws_demo", task["id"])].count(
        "task.started"
    ) == 1
    recorded = store.get_task_command(
        "ws_demo",
        task["id"],
        command["idempotency_key"],
    )
    assert recorded is not None
    assert recorded["result"] == started

    with pytest.raises(RepositoryConflict, match="different command"):
        store.execute_task_command(
            "ws_demo",
            task["id"],
            {**command, "expected_status": None},
        )
