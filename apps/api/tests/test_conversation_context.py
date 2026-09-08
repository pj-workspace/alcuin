from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from alcuin_api.config import Settings
from alcuin_api.context_composition import RunContextComposer
from alcuin_api.main import create_app
from alcuin_core.contracts import AgentDefinition
from support import create_test_store


def _wait_for_run(
    client: TestClient,
    run_id: str,
    headers: dict[str, str],
    *,
    terminal: set[str] | None = None,
) -> dict[str, Any]:
    expected = terminal or {"completed", "failed"}
    deadline = time.time() + 4
    body: dict[str, Any] = {}
    while time.time() < deadline:
        response = client.get(f"/v1/runs/{run_id}", headers=headers)
        assert response.status_code == 200
        body = response.json()
        if body["status"] in expected:
            return body
        time.sleep(0.02)
    raise AssertionError(f"Run did not reach {expected}: {body}")


def test_application_startup_recovers_interrupted_runs_once() -> None:
    store = create_test_store()
    agent = store.list_agents("ws_demo")[0]
    interrupted_thread = store.create_thread(
        "ws_demo", agent["id"], "Interrupted conversation", {}
    )
    interrupted = store.create_run(
        "ws_demo",
        interrupted_thread["id"],
        agent["current_version_id"],
        "Continue until the API restarts",
    )
    store.set_run_status("ws_demo", interrupted["id"], "running")
    store.append_event(
        "ws_demo",
        interrupted["id"],
        "message.delta",
        {"delta": "Persisted visible response"},
    )
    approval_thread = store.create_thread(
        "ws_demo", agent["id"], "Approval conversation", {}
    )
    approval_run = store.create_run(
        "ws_demo",
        approval_thread["id"],
        agent["current_version_id"],
        "Wait for explicit approval",
    )
    store.set_run_status(
        "ws_demo", approval_run["id"], "waiting_for_approval"
    )

    app = create_app(
        Settings(
            deepseek_api_key=None,
            openai_api_key=None,
            searxng_url=None,
            qdrant_url=None,
        ),
        store=store,
    )
    headers = {"X-Alcuin-Workspace": "ws_demo"}
    with TestClient(app) as client:
        recovered = client.get(
            f"/v1/runs/{interrupted['id']}", headers=headers
        ).json()
        preserved = client.get(
            f"/v1/runs/{approval_run['id']}", headers=headers
        ).json()
        assert recovered["status"] == "failed"
        assert preserved["status"] == "waiting_for_approval"
        assert [
            item["run_id"] for item in app.state.recovered_interrupted_runs
        ] == [interrupted["id"]]
        assert recovered["events"][-1]["type"] == "run.failed"
        assert recovered["events"][-1]["payload"]["code"] == "runtime_interrupted"

    with TestClient(app) as client:
        recovered_again = client.get(
            f"/v1/runs/{interrupted['id']}", headers=headers
        ).json()
        assert app.state.recovered_interrupted_runs == []
        assert len(
            [event for event in recovered_again["events"] if event["type"] == "run.failed"]
        ) == 1


def test_same_thread_context_is_persisted_replayed_and_workspace_isolated() -> None:
    provider_payloads: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        provider_payloads.append(payload)
        messages = payload["messages"]
        current = next(
            message for message in reversed(messages) if message["role"] == "user"
        )
        current_text = str(current["content"])
        await asyncio.sleep(0.04)
        body = (
            'data: {"choices":[{"delta":{"reasoning_content":"private planning"}}]}\n\n'
            f'data: {json.dumps({"choices": [{"delta": {"content": "Answer: "}}]})}\n\n'
            f'data: {json.dumps({"choices": [{"delta": {"content": current_text}}]})}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(
            200,
            text=body,
            headers={"content-type": "text/event-stream"},
        )

    store = create_test_store()
    app = create_app(
        Settings(
            deepseek_api_key="test-provider-key",
            deepseek_base_url="http://provider.test/v1",
            deepseek_protocol="chat_completions",
            searxng_url=None,
        ),
        store=store,
        provider_transport=httpx.MockTransport(handler),
    )
    headers = {"X-Alcuin-Workspace": "ws_demo"}
    with TestClient(app) as client:
        definition = client.get("/v1/agents/agt_starter", headers=headers).json()[
            "definition"
        ]
        instructions = definition["instructions"]
        thread = client.post(
            "/v1/threads",
            headers=headers,
            json={
                "agent_id": "agt_starter",
                "title": "Continuous conversation",
                "context": {"record": {"id": "REC-42"}},
            },
        ).json()

        first = client.post(
            f"/v1/threads/{thread['id']}/runs",
            headers=headers,
            json={"input": "First question"},
        )
        assert first.status_code == 202
        # The user message and queued Run are one transaction, so the same Thread cannot fork.
        conflict = client.post(
            f"/v1/threads/{thread['id']}/runs",
            headers=headers,
            json={"input": "Conflicting question"},
        )
        assert conflict.status_code == 409
        assert "active Run" in conflict.json()["detail"]
        first_body = _wait_for_run(client, first.json()["id"], headers)
        assert first_body["status"] == "completed"

        second = client.post(
            f"/v1/threads/{thread['id']}/runs",
            headers=headers,
            json={"input": "Second question"},
        )
        assert second.status_code == 202
        second_body = _wait_for_run(client, second.json()["id"], headers)
        assert second_body["status"] == "completed"

        assert len(provider_payloads) == 2
        second_messages = provider_payloads[1]["messages"]
        assert [message["role"] for message in second_messages] == [
            "system",
            "user",
            "assistant",
            "user",
        ]
        assert second_messages[1]["content"] == "First question"
        assert second_messages[2]["content"] == "Answer: First question"
        assert second_messages[3]["content"] == "Second question"
        assert second_messages[0]["content"].count(instructions) == 1
        assert all(
            instructions not in str(message["content"])
            for message in second_messages[1:]
        )

        detail = client.get(f"/v1/threads/{thread['id']}", headers=headers)
        assert detail.status_code == 200
        assert [message["role"] for message in detail.json()["messages"]] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]
        second_assistant = detail.json()["messages"][-1]
        persisted_text = "".join(
            part["text"]
            for part in second_assistant["parts"]
            if part["type"] == "text"
        )
        visible_deltas = "".join(
            event["payload"].get("delta", "")
            for event in second_body["events"]
            if event["type"] == "message.delta"
        )
        assert persisted_text == visible_deltas == "Answer: Second question"
        assert "private planning" not in persisted_text
        assert second_body["events"][0]["type"] == "run.started"
        assert second_body["events"][1]["type"] == "context.assembled"
        assert second_body["events"][-1]["type"] == "run.completed"

        context = client.get(
            f"/v1/runs/{second.json()['id']}/context",
            headers=headers,
        )
        assert context.status_code == 200
        assert "normalized_input" not in context.json()
        assert context.json()["entries"]
        assert set(context.json()["entries"][0]) == {
            "kind",
            "label",
            "source_ref",
            "source_version",
            "digest",
            "token_estimate",
            "included",
        }

        outside = {"X-Alcuin-Workspace": "ws_other"}
        assert client.get(f"/v1/threads/{thread['id']}", headers=outside).status_code == 404
        assert (
            client.get(
                f"/v1/threads/{thread['id']}/messages", headers=outside
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/v1/runs/{second.json()['id']}/context", headers=outside
            ).status_code
            == 404
        )

        isolated = client.post(
            "/v1/threads",
            headers=headers,
            json={"agent_id": "agt_starter", "title": "Isolated", "context": {}},
        ).json()
        isolated_run = client.post(
            f"/v1/threads/{isolated['id']}/runs",
            headers=headers,
            json={"input": "Other thread question"},
        ).json()
        assert _wait_for_run(client, isolated_run["id"], headers)["status"] == "completed"
        isolated_messages = provider_payloads[-1]["messages"]
        assert [message["role"] for message in isolated_messages] == ["system", "user"]
        assert all("First question" not in str(message) for message in isolated_messages)


@pytest.mark.asyncio
async def test_context_pressure_creates_traceable_overlay_without_deleting_messages() -> None:
    store = create_test_store()
    thread = store.create_thread("ws_demo", "agt_starter", "Long thread", {})
    version = store.get_agent_version("ws_demo", "av_starter_1")
    assert version is not None
    definition = AgentDefinition.model_validate(version["definition"])

    for index in range(6):
        prompt = f"Question {index}: " + ("context " * 30)
        run = store.create_run_with_messages(
            "ws_demo",
            thread["id"],
            "av_starter_1",
            prompt,
            [{"type": "text", "text": prompt}],
            90,
        )
        answer = f"Answer {index}: " + ("evidence " * 30)
        store.finalize_assistant_message(
            "ws_demo",
            run["id"],
            "completed",
            answer,
            90,
        )

    current = store.create_run_with_messages(
        "ws_demo",
        thread["id"],
        "av_starter_1",
        "Current question",
        [{"type": "text", "text": "Current question"}],
        10,
    )
    composer = RunContextComposer(
        store,
        Settings(
            context_window_tokens=8_192,
            context_reserved_output_tokens=256,
            context_reserved_tool_tokens=0,
            context_compaction_trigger_ratio=0.08,
        ),
    )
    composition = await composer.compose(
        workspace_id="ws_demo",
        run=current,
        thread=store.get_thread("ws_demo", thread["id"]) or {},
        definition=definition,
        platform_protocol="Render concise, valid Markdown.",
    )

    assert [event for event, _payload in composition.lifecycle_events] == [
        "context.compaction.started",
        "context.compaction.completed",
    ]
    assert composition.assembly.compaction_id is not None
    active = store.get_active_compaction("ws_demo", thread["id"])
    assert active is not None
    assert active["source_message_ids"]
    # Compaction is an overlay: every source and recent message remains immutable and queryable.
    assert len(store.list_messages("ws_demo", thread["id"], limit=500)) == 13
