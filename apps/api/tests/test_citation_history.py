from __future__ import annotations

import time

import pytest
from alcuin_api.config import Settings
from alcuin_api.main import create_app
from alcuin_api.security import issue_embed_token
from alcuin_core.contracts import EmbedClaims, ThreadDetail
from fastapi.testclient import TestClient
from pydantic import ValidationError
from support import create_test_store


def _finished_run(store, thread, version_id, number):
    run = store.create_run("ws_demo", thread["id"], version_id, f"Question {number}")
    event = store.append_event(
        "ws_demo",
        run["id"],
        "citation.created",
        {
            "citation_id": "s1",
            "locator": f"https://example.com/{number}",
            "snippet": f"Evidence {number}",
        },
    )
    store.append_event(
        "ws_demo", run["id"], "message.delta", {"delta": f"Answer {number} [[cite:s1]]"}
    )
    store.set_run_status("ws_demo", run["id"], "completed")
    return run, event


def test_recent_projection_and_lazy_history_keep_run_thread_and_workspace_boundaries():
    store = create_test_store()
    agent = store.list_agents("ws_demo")[0]
    version = agent["current_version_id"]
    thread = store.create_thread("ws_demo", agent["id"], "Source history", {})
    runs = [_finished_run(store, thread, version, index) for index in range(52)]
    other_thread = store.create_thread("ws_demo", agent["id"], "Other history", {})
    other_run, _ = _finished_run(store, other_thread, version, "private-thread")
    settings = Settings(
        deepseek_api_key=None, openai_api_key=None, searxng_url=None, qdrant_url=None
    )
    with TestClient(create_app(settings, store=store)) as client:
        headers = {"X-Alcuin-Workspace": "ws_demo"}
        response = client.get(f"/v1/threads/{thread['id']}", headers=headers)
        assert response.status_code == 200
        detail = response.json()
        assert len(detail["messages"]) == 52
        assert len(detail["runs"]) == 52
        assert len(detail["citation_events"]) == 50
        assert {event["run_id"] for event in detail["citation_events"]} == {
            run["id"] for run, _ in runs[-50:]
        }
        first_run, first_source = runs[0]
        historical = client.get(
            f"/v1/runs/{first_run['id']}/citations", headers=headers
        )
        assert historical.status_code == 200
        assert historical.json() == [first_source]
        assert all(event["type"] == "citation.created" for event in historical.json())
        assert store.list_thread_citation_events(
            "ws_demo", thread["id"], [first_run["id"], other_run["id"]]
        ) == [first_source]
        assert (
            store.list_thread_citation_events(
                "ws_other", thread["id"], [first_run["id"]]
            )
            == []
        )
        assert store.list_run_citation_events("ws_other", first_run["id"]) == []
        assert (
            client.get(
                f"/v1/runs/{first_run['id']}/citations",
                headers={"X-Alcuin-Workspace": "ws_other"},
            ).status_code
            == 404
        )
        assert client.get(f"/v1/runs/{first_run['id']}/citations").status_code == 401
        wrong_agent = EmbedClaims(
            workspace_id="ws_demo",
            agent_id="agt_not_authorized",
            agent_version_id=version,
            origin="https://host.example.com",
            allowed_actions=["run:read"],
            issued_at=int(time.time()),
            expires_at=int(time.time()) + 600,
        )
        assert (
            client.get(
                f"/v1/runs/{first_run['id']}/citations",
                headers={
                    "Authorization": f"Bearer {issue_embed_token(wrong_agent)}",
                    "Origin": wrong_agent.origin,
                },
            ).status_code
            == 403
        )


def test_projection_limits_and_legacy_source_payloads_are_preserved():
    store = create_test_store()
    try:
        agent = store.list_agents("ws_demo")[0]
        thread = store.create_thread("ws_demo", agent["id"], "Legacy sources", {})
        run = store.create_run(
            "ws_demo", thread["id"], agent["current_version_id"], "Read"
        )
        for number in range(130):
            store.append_event(
                "ws_demo",
                run["id"],
                "citation.created",
                {"locator": f"https://example.com/{number}"},
            )
        assert (
            len(store.list_thread_citation_events("ws_demo", thread["id"], [run["id"]]))
            == 128
        )
        legacy = store.list_run_citation_events("ws_demo", run["id"])
        assert len(legacy) == 128
        assert all("citation_id" not in event["payload"] for event in legacy)
        assert store.list_thread_citation_events("ws_demo", thread["id"], []) == []
        with pytest.raises(ValueError, match="50 Runs"):
            store.list_thread_citation_events(
                "ws_demo", thread["id"], [str(index) for index in range(51)]
            )
    finally:
        store.close()


def test_thread_detail_contract_rejects_foreign_trace_events():
    body = {"thread": {"id": "thread-one"}, "messages": [], "runs": [{"id": "run-one"}]}
    assert ThreadDetail.model_validate(body).citation_events == []
    event = {
        "id": "source-one",
        "run_id": "run-one",
        "sequence": 1,
        "type": "citation.created",
        "timestamp": "2026-09-08T00:00:00Z",
        "payload": {"citation_id": "s1"},
    }
    assert (
        len(
            ThreadDetail.model_validate(
                {**body, "citation_events": [event]}
            ).citation_events
        )
        == 1
    )
    for invalid in [
        {**event, "run_id": "run-other"},
        {**event, "type": "reasoning.delta"},
    ]:
        with pytest.raises(ValidationError, match="Thread evidence"):
            ThreadDetail.model_validate({**body, "citation_events": [invalid]})
