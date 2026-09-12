"""PostgreSQL-backed naming triggers, ownership, and lightweight polling."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from alcuin_api.config import Settings
from alcuin_api.main import create_app
from support import create_test_store


HEADERS = {"X-Alcuin-Workspace": "ws_demo"}


@pytest.fixture
def store():
    repository = create_test_store()
    try:
        yield repository
    finally:
        repository.close()


def app_for(store):
    return create_app(
        Settings(
            _env_file=None,
            deepseek_api_key="",
            openai_api_key="",
            searxng_url="",
            qdrant_url="",
            dashscope_api_key="",
        ),
        store=store,
    )


def wait_title(client, thread_id):
    for _ in range(30):
        response = client.get(f"/v1/threads/{thread_id}/title", headers=HEADERS)
        assert response.status_code == 200
        if response.json()["title_status"] == "ready":
            return response.json()
    pytest.fail("Naming did not settle")


@pytest.mark.parametrize("kind", ["run", "task"])
def test_first_accepted_input_names_thread_without_waiting_for_execution(store, kind):
    with TestClient(app_for(store)) as client:
        thread = client.post(
            "/v1/threads", headers=HEADERS, json={"agent_id": "agt_starter"}
        ).json()
        assert thread["title_status"] == "pending"
        prefix = f"/v1/threads/{thread['id']}"
        assert (
            client.post(f"{prefix}/title/ensure", headers=HEADERS).json()[
                "title_status"
            ]
            == "pending"
        )
        goal = "整理通用智能体的验证方案"
        if kind == "run":
            response = client.post(
                f"{prefix}/runs", headers=HEADERS, json={"input": goal}
            )
        else:
            response = client.post(
                f"{prefix}/tasks",
                headers=HEADERS,
                json={"goal": goal, "steps": [{"title": "整理证据"}]},
            )
        assert response.status_code == (202 if kind == "run" else 201), response.text
        named = wait_title(client, thread["id"])
        assert named["title"] == goal
        assert "title_claim" not in named and "title_claim_until" not in named
        assert named["updated_at"] >= thread["updated_at"]


def test_get_title_never_reads_messages_or_starts_naming_and_is_scoped(
    store, monkeypatch
):
    with TestClient(app_for(store)) as client:
        thread = client.post(
            "/v1/threads",
            headers=HEADERS,
            json={"agent_id": "agt_starter", "title": "引用与文档验收"},
        ).json()
        monkeypatch.setattr(
            store, "list_messages", lambda *a, **kw: pytest.fail("GET read messages")
        )
        url = f"/v1/threads/{thread['id']}/title"
        assert client.get(url, headers=HEADERS).json()["title"] == "引用与文档验收"
        assert (
            client.post(url + "/ensure", headers=HEADERS).json()["title_status"]
            == "ready"
        )
        other = {"X-Alcuin-Workspace": "ws_other"}
        assert client.get(url, headers=other).status_code == 404
        assert client.post(url + "/ensure", headers=other).status_code == 404


def test_naming_claim_cas_and_stale_lease_repair(store):
    thread = store.create_thread("ws_demo", "agt_starter", "Working session", {})
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(
            pool.map(
                lambda claim: store.claim_thread_title(
                    "ws_demo", thread["id"], "Working session", claim
                ),
                ["one", "two"],
            )
        )
    assert sum(claims) == 1
    winner = "one" if claims[0] else "two"
    assert not store.finish_thread_title(
        "ws_other", thread["id"], "Working session", winner, "Cross workspace"
    )
    with store.lock, store.connection:
        store.connection.execute(
            "UPDATE threads SET title_claim_until = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", thread["id"]),
        )
    assert store.claim_thread_title(
        "ws_demo", thread["id"], "Working session", "repair"
    )
    assert not store.finish_thread_title(
        "ws_demo", thread["id"], "Working session", winner, "Stale result"
    )
    with store.lock, store.connection:
        store.connection.execute(
            "UPDATE threads SET title = ?, title_status = 'ready' WHERE id = ?",
            ("My custom title", thread["id"]),
        )
    assert not store.finish_thread_title(
        "ws_demo", thread["id"], "Working session", "repair", "Generated title"
    )
    assert store.get_thread("ws_demo", thread["id"])["title"] == "My custom title"


def test_repair_uses_saved_user_text_but_ignores_attachment_and_tool_parts(store):
    thread = store.create_thread("ws_demo", "agt_starter", "Working session", {})
    store.create_run_with_messages(
        "ws_demo",
        thread["id"],
        thread["agent_version_id"],
        "Saved first request",
        [
            {"type": "text", "text": "Saved first request"},
            {
                "type": "task_instruction",
                "text": "Never summarize this execution instruction",
            },
        ],
        20,
    )
    with TestClient(app_for(store)) as client:
        assert (
            client.post(
                f"/v1/threads/{thread['id']}/title/ensure", headers=HEADERS
            ).status_code
            == 200
        )
        assert wait_title(client, thread["id"])["title"] == "Saved first request"
        assert store.thread_title_candidates("ws_other", thread["id"]) == []
