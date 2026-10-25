"""Real HTTP cancellation boundaries with deterministic provider/tool gates."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient

from alcuin_api.config import Settings
from alcuin_api.main import create_app
from alcuin_core.tools import ToolCitation
from alcuin_operations_copilot import operations_demo_adapter, seed_operations_demo
from support import create_test_store
from test_approval_continuation import approval_client, start_approval, stream_response
from test_conversation_context import _wait_for_run
from test_human_input import question_client, start_question
from test_task_hardening_races import _waiting_approval_fixture

HEADERS = {"X-Alcuin-Workspace": "ws_demo"}


class Gate:
    def __init__(self):
        self.entered = Event()
        self.exited = Event()
        self.release = None

    async def wait(self):
        self.release = asyncio.Event()
        self.entered.set()
        try:
            await self.release.wait()
        finally:
            self.exited.set()

    def open(self, client):
        if self.release is not None:
            client.portal.call(self.release.set)


def start_run(client):
    thread = client.post("/v1/threads", headers=HEADERS, json={"agent_id": "agt_operations"}).json()
    response = client.post(f"/v1/threads/{thread['id']}/runs", headers=HEADERS,
                           json={"input": "Read the incident and prepare a report"})
    assert response.status_code == 202, response.text
    return thread, response.json()


def assert_cancelled_once(snapshot):
    assert snapshot["status"] == "cancelled", snapshot
    types = [event["type"] for event in snapshot["events"]]
    assert types.count("run.cancel_requested") == 1
    assert types.count("run.cancelled") == 1
    assert "run.completed" not in types
    assert "run.failed" not in types
    assert types[-1] == "run.cancelled"


@pytest.mark.parametrize("waiting", ["approval", "input"])
def test_cancel_waiting_run_closes_old_action_and_survives_reload(waiting):
    if waiting == "approval":
        client, payloads, executions = approval_client()
    else:
        client, payloads = question_client()
    with client:
        if waiting == "approval":
            thread, run, pending = start_approval(client)
            stale_url = f"/v1/runs/{run['id']}/approvals/{pending['approval_id']}"
            stale_body = {"decision": "approved"}
        else:
            thread, run, pending = start_question(client)
            stale_url = f"/v1/runs/{run['id']}/inputs/{pending['input_id']}"
            stale_body = {"answer": "Engineering"}
        calls_before = len(payloads)
        url = f"/v1/runs/{run['id']}/cancel"
        assert client.post(url, headers={"X-Alcuin-Workspace": "ws_other"}).status_code == 404
        response = client.post(url, headers=HEADERS)
        assert response.status_code == 200, response.text
        assert response.json()["status"] in {"cancel_requested", "cancelled"}
        done = _wait_for_run(client, run["id"], HEADERS, terminal={"cancelled"})
        assert_cancelled_once(done)
        assert client.post(stale_url, headers=HEADERS, json=stale_body).status_code == 409
        assert len(payloads) == calls_before
        if waiting == "approval":
            assert all(name != "ops.update_ticket" for name, _ in executions)
        repeated = client.post(url, headers=HEADERS)
        assert repeated.status_code == 200
        assert repeated.json()["status"] == "cancelled"
        assert client.get(f"/v1/runs/{run['id']}", headers=HEADERS).json()["events"] == done["events"]
        for protocol in ("execution", "chat"):
            stream = client.get(f"/v1/runs/{run['id']}/events?protocol={protocol}", headers=HEADERS)
            assert stream.status_code == 200
            assert "[DONE]" in stream.text or '"finish"' in stream.text
        detail = client.get(f"/v1/threads/{thread['id']}", headers=HEADERS).json()
        assert "checkpoint_json" not in json.dumps(detail)
        assert client.post(f"/v1/threads/{thread['id']}/runs", headers=HEADERS,
                           json={"input": "follow-up"}).status_code == 202


def test_cancel_stream_retains_partial_answer_artifact_and_citation():
    store = create_test_store()
    seed_operations_demo(store)
    gate = Gate()
    payloads = []

    class PartialStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            content = '<alcuin-answer>Partial report.<alcuin-artifact title="Draft" content-type="text/markdown"># Saved draft</alcuin-artifact>'
            yield ("data: " + json.dumps({"choices": [{"delta": {"content": content}}]}) + "\n\n").encode()
            await gate.wait()
            yield b'data: {"choices":[{"delta":{"content":"Late text.</alcuin-answer>"}}]}\n\n'
            yield b"data: [DONE]\n\n"

    async def provider(request):
        body = json.loads(request.content)
        if not body.get("tools"):
            return stream_response(content="Incident report")
        payloads.append(body)
        if len(payloads) == 1:
            return stream_response([("read", "ops_search_incidents", {"query": "current incident"})])
        return httpx.Response(200, stream=PartialStream(), headers={"content-type": "text/event-stream"})

    async def adapter(context, tool_name, arguments):
        result = await operations_demo_adapter(context, tool_name, arguments)
        return replace(result, citations=(ToolCitation("Incident evidence", "Operations Toolkit", "ops://incidents/INC-104"),))

    app = create_app(Settings(deepseek_api_key="test", deepseek_base_url="http://provider.test/v1",
                              deepseek_protocol="chat_completions", openai_api_key=None),
                     store=store, provider_transport=httpx.MockTransport(provider),
                     builtin_adapters={"operations-demo": adapter})
    with TestClient(app) as client:
        thread, run = start_run(client)
        try:
            assert gate.entered.wait(3), "Provider never reached its stream boundary"
            before = client.get(f"/v1/runs/{run['id']}", headers=HEADERS).json()
            preserved = [event for event in before["events"] if event["type"] in {"artifact.updated", "citation.created"}]
            assert {event["type"] for event in preserved} == {"artifact.updated", "citation.created"}
            response = client.post(f"/v1/runs/{run['id']}/cancel", headers=HEADERS)
            assert response.status_code == 200, response.text
            done = _wait_for_run(client, run["id"], HEADERS, terminal={"cancelled"})
            assert gate.exited.wait(1), "Cancellation did not interrupt the provider wait"
            assert_cancelled_once(done)
            assert [event for event in done["events"] if event["type"] in {"artifact.updated", "citation.created"}] == preserved
            assert "".join(event["payload"].get("delta", "") for event in done["events"] if event["type"] == "message.delta") == "Partial report."
            detail = client.get(f"/v1/threads/{thread['id']}", headers=HEADERS).json()
            assistant = [message for message in detail["messages"] if message["role"] == "assistant"]
            assert len(assistant) == 1
            assert "Partial report." in json.dumps(assistant)
            assert "Late text" not in json.dumps(detail)
            assert len(payloads) == 2
            gate.open(client)
            assert client.get(f"/v1/runs/{run['id']}", headers=HEADERS).json()["events"] == done["events"]
            artifact_id = next(event["payload"]["artifact"]["id"] for event in preserved if event["type"] == "artifact.updated")
            artifact_url = f"/v1/artifacts/{artifact_id}"
            fetched = client.get(artifact_url, headers=HEADERS)
            assert fetched.status_code == 200, fetched.text
            artifact = fetched.json()
            download = client.get(artifact_url + "/download?format=md", headers=HEADERS)
            assert download.status_code == 200, download.text
            assert "# Saved draft" in download.text
            edited = client.patch(artifact_url, headers=HEADERS, json={
                "expected_version": artifact["version"], "content": "# Reviewed stopped draft",
            })
            assert edited.status_code == 200, edited.text
            assert edited.json()["version"] == artifact["version"] + 1
            assert client.get(artifact_url, headers=HEADERS).json()["content"] == "# Reviewed stopped draft"
            edited_download = client.get(artifact_url + "/download?format=md", headers=HEADERS)
            assert edited_download.status_code == 200
            assert "# Reviewed stopped draft" in edited_download.text
            after_edit = client.get(f"/v1/runs/{run['id']}", headers=HEADERS).json()
            assert after_edit["status"] == "cancelled"
            assert [event for event in after_edit["events"] if event["type"] == "citation.created"] == [event for event in preserved if event["type"] == "citation.created"]
        finally:
            gate.open(client)


def test_cancel_inflight_mutation_waits_for_receipt_and_prevents_next_model_call():
    store = create_test_store()
    seed_operations_demo(store)
    gate = Gate()
    payloads, effects = [], []

    async def provider(request):
        body = json.loads(request.content)
        if not body.get("tools"):
            return stream_response(content="Ticket update")
        payloads.append(body)
        if len(payloads) == 1:
            return stream_response([("mutation", "ops_update_ticket", {"ticket_id": "INC-104", "status": "resolved"})])
        return stream_response(content="Must not resume after stop")

    async def adapter(context, tool_name, arguments):
        effects.append((tool_name, arguments))
        await gate.wait()
        return await operations_demo_adapter(context, tool_name, arguments)

    app = create_app(Settings(deepseek_api_key="test", deepseek_base_url="http://provider.test/v1",
                              deepseek_protocol="chat_completions", openai_api_key=None),
                     store=store, provider_transport=httpx.MockTransport(provider),
                     builtin_adapters={"operations-demo": adapter})
    with TestClient(app) as client:
        _, run, approval = start_approval(client)
        try:
            response = client.post(f"/v1/runs/{run['id']}/approvals/{approval['approval_id']}", headers=HEADERS,
                                   json={"decision": "approved"})
            assert response.status_code == 200, response.text
            assert gate.entered.wait(3)
            url = f"/v1/runs/{run['id']}/cancel"
            response = client.post(url, headers=HEADERS)
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "cancel_requested"
            requested = next(event for event in response.json()["events"] if event["type"] == "run.cancel_requested")
            assert requested["payload"]["tool_in_flight"] is True
            assert not gate.exited.is_set(), "An already-dispatched mutation was interrupted"
            assert client.post(url, headers=HEADERS).status_code == 200
            gate.open(client)
            done = _wait_for_run(client, run["id"], HEADERS, terminal={"cancelled"})
            assert_cancelled_once(done)
            receipts = [event for event in done["events"] if event["type"] == "tool.completed"]
            assert len(receipts) == 1
            assert receipts[0]["payload"]["status"] == "succeeded"
            assert receipts[0]["sequence"] < done["events"][-1]["sequence"]
            assert done["events"][-1]["payload"]["external_actions_may_have_completed"] is True
            assert len(effects) == 1
            assert len(payloads) == 1
        finally:
            gate.open(client)


def test_cancel_after_approval_decision_before_dispatch_never_executes_mutation(monkeypatch):
    client, payloads, executions = approval_client()
    gate = Gate()
    with client:
        _, run, approval = start_approval(client)
        runtime = client.app.state.runtime
        original = runtime.continue_approval_in_background

        async def delayed_resume(*args, **kwargs):
            await gate.wait()
            await original(*args, **kwargs)

        monkeypatch.setattr(runtime, "continue_approval_in_background", delayed_resume)
        try:
            response = client.post(f"/v1/runs/{run['id']}/approvals/{approval['approval_id']}", headers=HEADERS,
                                   json={"decision": "approved"})
            assert response.status_code == 200, response.text
            assert gate.entered.wait(3)
            response = client.post(f"/v1/runs/{run['id']}/cancel", headers=HEADERS)
            assert response.status_code == 200, response.text
            gate.open(client)
            done = _wait_for_run(client, run["id"], HEADERS, terminal={"cancelled"})
            assert_cancelled_once(done)
            assert len(payloads) == 2
            assert [name for name, _ in executions] == ["ops.search_incidents"]
        finally:
            gate.open(client)


def test_cancel_completed_run_returns_existing_result_without_new_events():
    client, _, _ = approval_client()
    with client:
        _, run, approval = start_approval(client)
        response = client.post(f"/v1/runs/{run['id']}/approvals/{approval['approval_id']}", headers=HEADERS,
                               json={"decision": "denied"})
        assert response.status_code == 200
        done = _wait_for_run(client, run["id"], HEADERS)
        assert done["status"] == "completed"
        response = client.post(f"/v1/runs/{run['id']}/cancel", headers=HEADERS)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "completed"
        assert response.json()["events"] == done["events"]


def test_cancel_queued_worker_cannot_start_provider_or_tools(monkeypatch):
    client, payloads, executions = approval_client()
    gate = Gate()
    with client:
        runtime = client.app.state.runtime
        original = runtime.execute

        async def delayed_execute(*args, **kwargs):
            await gate.wait()
            await original(*args, **kwargs)

        monkeypatch.setattr(runtime, "execute", delayed_execute)
        thread, run = start_run(client)
        try:
            assert gate.entered.wait(3)
            queued = client.get(f"/v1/runs/{run['id']}", headers=HEADERS).json()
            assert queued["status"] == "queued"
            response = client.post(f"/v1/runs/{run['id']}/cancel", headers=HEADERS)
            assert response.status_code == 200, response.text
            gate.open(client)
            done = _wait_for_run(client, run["id"], HEADERS, terminal={"cancelled"})
            assert_cancelled_once(done)
            assert not any(event["type"] == "run.started" for event in done["events"])
            assert payloads == []
            assert executions == []
            messages = client.get(f"/v1/threads/{thread['id']}", headers=HEADERS).json()["messages"]
            stopped = next(message for message in messages if message["id"] == done["output_message_id"])
            assert stopped["status"] == "cancelled"
            assert stopped["parts"] == [{"type": "text", "text": ""}]
            original_input = next(message for message in messages if message["role"] == "user")
            assert original_input["parts"] == [{"type": "text", "text": "Read the incident and prepare a report"}]
            monkeypatch.setattr(runtime, "execute", original)
            followup = client.post(f"/v1/threads/{thread['id']}/runs", headers=HEADERS, json={"input": "Explain the previous stop"}).json()
            _wait_for_run(client, followup["id"], HEADERS, terminal={"waiting_for_approval", "completed", "failed"})
            prior_assistant = next(message for message in payloads[0]["messages"] if message["role"] == "assistant")
            assert "incomplete" in prior_assistant["content"]
            assert "Do not automatically retry previously started actions" in prior_assistant["content"]
        finally:
            gate.open(client)


def test_ordinary_cancel_cannot_change_a_task_owned_run():
    client, _, _ = approval_client()
    with client:
        store = client.app.state.store
        fixture = _waiting_approval_fixture(store)
        run_id = fixture["run"]["id"]
        task_id = fixture["task"]["id"]
        before_run = client.get(f"/v1/runs/{run_id}", headers=HEADERS).json()
        before_task = client.get(f"/v1/tasks/{task_id}", headers=HEADERS).json()
        response = client.post(f"/v1/runs/{run_id}/cancel", headers=HEADERS)
        assert response.status_code == 409, response.text
        assert client.get(f"/v1/runs/{run_id}", headers=HEADERS).json() == before_run
        assert client.get(f"/v1/tasks/{task_id}", headers=HEADERS).json() == before_task
        assert store.get_approval("ws_demo", fixture["approval"]["id"])["status"] == "pending"


def test_embed_cannot_cancel_even_with_access_to_the_run():
    client, _, _ = approval_client()
    with client:
        _, run, _ = start_approval(client)
        session = client.post("/v1/embed/sessions", headers=HEADERS,
                              json={"agent_id": "agt_operations", "origin": "http://localhost:3000"})
        assert session.status_code == 201, session.text
        embed = {"Authorization": f"Bearer {session.json()['token']}", "Origin": "http://localhost:3000"}
        visible = client.get(f"/v1/runs/{run['id']}", headers=embed)
        assert visible.status_code == 200, visible.text
        before = visible.json()
        response = client.post(f"/v1/runs/{run['id']}/cancel", headers=embed)
        assert response.status_code == 403, response.text
        assert client.get(f"/v1/runs/{run['id']}", headers=HEADERS).json() == before


def test_cancel_interrupts_readonly_tool_wait_without_fabricating_a_receipt():
    store = create_test_store()
    seed_operations_demo(store)
    gate = Gate()
    payloads, entered_tools, returned_tools = [], [], []

    async def provider(request):
        body = json.loads(request.content)
        if not body.get("tools"):
            return stream_response(content="Incident lookup")
        payloads.append(body)
        if len(payloads) == 1:
            return stream_response([("read", "ops_search_incidents", {"query": "current incident"})])
        return stream_response(content="Must not continue after stopping the read")

    async def adapter(context, tool_name, arguments):
        entered_tools.append(tool_name)
        await gate.wait()
        result = await operations_demo_adapter(context, tool_name, arguments)
        returned_tools.append(tool_name)
        return result

    app = create_app(Settings(deepseek_api_key="test", deepseek_base_url="http://provider.test/v1",
                              deepseek_protocol="chat_completions", openai_api_key=None),
                     store=store, provider_transport=httpx.MockTransport(provider),
                     builtin_adapters={"operations-demo": adapter})
    with TestClient(app) as client:
        _, run = start_run(client)
        try:
            assert gate.entered.wait(3), "Read-only tool never began"
            response = client.post(f"/v1/runs/{run['id']}/cancel", headers=HEADERS)
            assert response.status_code == 200, response.text
            done = _wait_for_run(client, run["id"], HEADERS, terminal={"cancelled"})
            assert gate.exited.wait(1), "Cancellation did not interrupt the read-only wait"
            assert_cancelled_once(done)
            assert not any(event["type"] == "tool.completed" for event in done["events"])
            assert done["events"][-1]["payload"]["external_actions_may_have_completed"] is False
            assert entered_tools == ["ops.search_incidents"]
            assert returned_tools == []
            assert len(payloads) == 1
            gate.open(client)
            assert client.get(f"/v1/runs/{run['id']}", headers=HEADERS).json()["events"] == done["events"]
        finally:
            gate.open(client)
