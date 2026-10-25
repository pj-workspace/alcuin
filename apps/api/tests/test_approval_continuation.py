"""Real Studio approval boundaries with a deterministic streaming provider."""
import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from alcuin_api.config import Settings
from alcuin_api.main import create_app
from alcuin_core.tools import ToolCitation
from alcuin_operations_copilot import operations_demo_adapter, seed_operations_demo
from alcuin_storage import RepositoryConflict
from support import create_test_store
from test_conversation_context import _wait_for_run

HEADERS = {"X-Alcuin-Workspace": "ws_demo"}


def stream_response(calls=(), content="Approval decision incorporated into the final report."):
    delta = {"tool_calls": [
        {"index": index, "id": call_id, "function": {"name": name, "arguments": json.dumps(arguments)}}
        for index, (call_id, name, arguments) in enumerate(calls)
    ]} if calls else {"content": content}
    return httpx.Response(200, text=f"data: {json.dumps({'choices': [{'delta': delta}]})}\n\ndata: [DONE]\n\n",
                          headers={"content-type": "text/event-stream"})


def approval_client(request_sibling_again=False):
    store = create_test_store()
    seed_operations_demo(store)
    payloads, executions = [], []

    async def adapter(context, tool_name, arguments):
        executions.append((tool_name, dict(arguments)))
        result = await operations_demo_adapter(context, tool_name, arguments)
        if tool_name == "ops.update_ticket":
            assert context.mutation_authorized
            return replace(result, citations=(ToolCitation("Update receipt", "Operations Toolkit", "ops://receipts/INC-104"),))
        return result

    async def provider(request):
        body = json.loads(request.content)
        if not body.get("tools"):
            return stream_response(content="Ticket update")
        payloads.append(body)
        if len(payloads) == 1:
            return stream_response([("read", "ops_search_incidents", {"query": "current incident"})])
        if len(payloads) == 2:
            return stream_response([
                ("mutation", "ops_update_ticket", {"ticket_id": "INC-104", "status": "resolved"}),
                ("deferred", "ops_search_incidents", {"query": "verify after decision"}),
            ])
        if len(payloads) == 3 and request_sibling_again:
            # A new model request, not automatic execution of the suspended sibling.
            return stream_response([("fresh-read", "ops_search_incidents", {"query": "verify after decision"})])
        return stream_response()

    app = create_app(Settings(deepseek_api_key="test-provider-key", deepseek_base_url="http://provider.test/v1",
                              deepseek_protocol="chat_completions", openai_api_key=None),
                     store=store, provider_transport=httpx.MockTransport(provider),
                     builtin_adapters={"operations-demo": adapter})
    return TestClient(app), payloads, executions


def start_approval(client):
    thread = client.post("/v1/threads", headers=HEADERS, json={"agent_id": "agt_operations"}).json()
    response = client.post(f"/v1/threads/{thread['id']}/runs", headers=HEADERS, json={"input": "Resolve the ticket and summarize the result"})
    assert response.status_code == 202, response.text
    run = response.json()
    snapshot = _wait_for_run(client, run["id"], HEADERS, terminal={"waiting_for_approval", "failed"})
    assert snapshot["status"] == "waiting_for_approval", snapshot
    approval = next(e["payload"] for e in snapshot["events"] if e["type"] == "approval.required")
    return thread, run, approval


@pytest.mark.parametrize("decision", ["approved", "denied"])
@pytest.mark.parametrize("request_sibling_again", [False, True])
def test_studio_approval_resumes_same_model_loop_without_replaying_tools(decision, request_sibling_again):
    client, payloads, executions = approval_client(request_sibling_again)
    with client:
        thread, run, approval = start_approval(client)
        store = client.app.state.store
        assert executions == [("ops.search_incidents", {"query": "current incident"})]
        checkpoint = json.loads(store._one("SELECT checkpoint_json FROM approval_continuations WHERE approval_id = ?", (approval["approval_id"],))["checkpoint_json"])
        assert checkpoint["next_step"] == 2
        assert checkpoint["tool_call_counts"] == {"ops.search_incidents": 1, "ops.update_ticket": 1}
        url = f"/v1/runs/{run['id']}/approvals/{approval['approval_id']}"
        body = {"decision": decision, "note": "Reviewed the intended update"}
        assert client.post(url, headers={"X-Alcuin-Workspace": "ws_other"}, json=body).status_code == 404
        assert client.post(f"/v1/threads/{thread['id']}/runs", headers=HEADERS, json={"input": "another"}).status_code == 409
        store.recover_interrupted_runs()
        assert store.get_run("ws_demo", run["id"])["status"] == "waiting_for_approval"
        public = json.dumps(client.get(f"/v1/runs/{run['id']}", headers=HEADERS).json())
        public += json.dumps(client.get(f"/v1/threads/{thread['id']}", headers=HEADERS).json())
        public += client.get(f"/v1/runs/{run['id']}/events", headers=HEADERS).text
        for private_key in ("checkpoint_json", "continuation", "next_step", "tool_call_counts"):
            assert private_key not in public
        response = client.post(url, headers=HEADERS, json=body)
        assert response.status_code == 200, response.text
        done = _wait_for_run(client, run["id"], HEADERS)
        assert done["status"] == "completed", done
        assert done["id"] == run["id"]
        assert len(payloads) == 3 + int(request_sibling_again)
        results = {m["tool_call_id"]: json.loads(m["content"]) for m in payloads[2]["messages"] if m["role"] == "tool"}
        assert set(results) == {"read", "mutation", "deferred"}
        assert results["deferred"]["status"] == "deferred"
        if decision == "approved":
            assert results["mutation"]["data"]["updated"] is True
            evidence = results["mutation"]["evidence"]
            assert evidence[0]["citation_id"]
            assert evidence[0]["locator"] == "ops://receipts/INC-104"
            assert any(e["type"] == "citation.created" and e["payload"].get("citation_id") == evidence[0]["citation_id"] for e in done["events"])
        else:
            assert results["mutation"]["status"] == "denied"
        expected = [("ops.search_incidents", {"query": "current incident"})]
        if decision == "approved":
            expected.append(("ops.update_ticket", {"ticket_id": "INC-104", "status": "resolved"}))
        if request_sibling_again:
            expected.append(("ops.search_incidents", {"query": "verify after decision"}))
        assert executions == expected
        assert sum(e["type"] == "run.started" for e in done["events"]) == 1
        assert sum(e["type"] == "run.completed" for e in done["events"]) == 1
        assert any(e["type"] == "message.delta" and "final report" in e["payload"].get("delta", "") for e in done["events"])
        assert not any(e["type"] == "tool.completed" and e["payload"].get("call_id") == "deferred" for e in done["events"])
        assert store._one("SELECT checkpoint_json FROM approval_continuations WHERE approval_id = ?", (approval["approval_id"],))["checkpoint_json"] is None
        assert client.post(url, headers=HEADERS, json=body).status_code == 409
        assert executions == expected


def test_approval_suspend_is_atomic_and_checkpoint_claim_is_scoped_one_shot(monkeypatch):
    store = create_test_store()
    try:
        agent = store.list_agents("ws_demo")[0]
        thread = store.create_thread("ws_demo", agent["id"], "Approval storage", {})
        run = store.create_run("ws_demo", thread["id"], agent["current_version_id"], "Update")
        store.set_run_status("ws_demo", run["id"], "running")
        request = {"tool": "ops.update_ticket", "call_id": "mutation", "arguments": {"ticket_id": "INC-104", "status": "resolved"}}
        checkpoint = {"adapter": "openai-chat-v1", "messages": [], "next_step": 2, "tool_call_counts": {"ops.update_ticket": 1}}
        original = store._append_event_locked

        def fail_after_append(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("injected suspension rollback")

        with monkeypatch.context() as patch:
            patch.setattr(store, "_append_event_locked", fail_after_append)
            with pytest.raises(RuntimeError, match="injected suspension"):
                store.suspend_run_for_approval("ws_demo", run["id"], request, checkpoint)
        assert store.get_run("ws_demo", run["id"])["status"] == "running"
        assert store._one("SELECT id FROM approvals WHERE run_id = ?", (run["id"],)) is None
        assert store._one("SELECT approval_id FROM approval_continuations WHERE run_id = ?", (run["id"],)) is None
        assert not any(e["type"] == "approval.required" for e in store.list_events("ws_demo", run["id"]))
        event = store.suspend_run_for_approval("ws_demo", run["id"], request, checkpoint)
        approval_id = event["payload"]["approval_id"]
        with pytest.raises(RepositoryConflict):
            store.take_approval_continuation("ws_demo", run["id"], approval_id)
        store.decide_approval("ws_demo", approval_id, "approved", "Reviewed")
        for workspace_id, run_id in [("ws_other", run["id"]), ("ws_demo", "other-run")]:
            with pytest.raises(RepositoryConflict):
                store.take_approval_continuation(workspace_id, run_id, approval_id)
        assert store.take_approval_continuation("ws_demo", run["id"], approval_id) == checkpoint
        with pytest.raises(RepositoryConflict):
            store.take_approval_continuation("ws_demo", run["id"], approval_id)
        # An interrupted decision must not leave a replayable private transcript.
        other_thread = store.create_thread("ws_demo", agent["id"], "Interrupted approval", {})
        interrupted = store.create_run("ws_demo", other_thread["id"], agent["current_version_id"], "Update")
        store.set_run_status("ws_demo", interrupted["id"], "running")
        pending = store.suspend_run_for_approval("ws_demo", interrupted["id"], request, checkpoint)
        pending_id = pending["payload"]["approval_id"]
        store.decide_approval("ws_demo", pending_id, "approved", "Reviewed")
        store.recover_interrupted_runs()
        assert store.get_run("ws_demo", interrupted["id"])["status"] == "failed"
        assert store._one("SELECT checkpoint_json FROM approval_continuations WHERE approval_id = ?", (pending_id,))["checkpoint_json"] is None
        with pytest.raises(RepositoryConflict):
            store.take_approval_continuation("ws_demo", interrupted["id"], pending_id)
    finally:
        store.close()


@pytest.mark.parametrize("budget", ["steps", "tool_calls", "last_step"])
async def test_approval_continuation_preserves_runtime_budgets(budget):
    from alcuin_api.runtime import OpenAICompatibleRuntime, RuntimeRequest
    from alcuin_api.tools import ToolDefinition, ToolExecutor, ToolRegistry
    from alcuin_core.contracts import AgentDefinition

    payloads = []

    async def handler(request):
        payloads.append(json.loads(request.content))
        return stream_response([("mutation", "ops_update_ticket", {"ticket_id": "INC-104", "status": "resolved"})])

    async def mutation(context, arguments):
        pytest.fail("A pending or exhausted mutation must not execute")

    registry = ToolRegistry([ToolDefinition("ops.update_ticket", "Update ticket", {"type": "object"}, mutation,
                                            mutating=True, max_calls_per_run=2 if budget == "last_step" else 1)])
    runtime = OpenAICompatibleRuntime(Settings(deepseek_api_key="test", deepseek_protocol="chat_completions"),
                                     httpx.MockTransport(handler), tool_executor=ToolExecutor(registry))
    request = RuntimeRequest(workspace_id="ws_demo", run_id="run_budget", prompt="Update", thread_context={}, interactive=True,
                             definition=AgentDefinition(identity={"name": "Budget"}, instructions="Update when approved",
                                 tools=["ops.update_ticket"], policies={"mutating_tools": "ask"},
                                 model={"provider": "deepseek", "model": "test"}, runtime={"max_steps": 2}))
    first = [event async for event in runtime.stream(request)]
    checkpoint = first[-1].continuation
    assert checkpoint["next_step"] == 1
    assert checkpoint["tool_call_counts"] == {"ops.update_ticket": 1}
    checkpoint["messages"].append({"role": "tool", "tool_call_id": "mutation", "content": '{"updated":true}'})
    if budget == "steps":
        checkpoint["next_step"] = 2
    resumed = []
    with pytest.raises(RuntimeError, match="max_steps=2"):
        async for event in runtime.stream(replace(request, continuation=checkpoint)):
            resumed.append(event)
    assert len(payloads) == (1 if budget == "steps" else 2)
    assert not any(event.type == "approval.required" for event in resumed)
    if budget == "tool_calls":
        assert "tools" not in payloads[-1]
        assert any(event.type == "tool.completed" and event.payload.get("error", {}).get("code") == "tool_budget_exceeded" for event in resumed)
    if budget == "last_step":
        assert any(event.type == "tool.completed" and event.payload.get("error", {}).get("code") == "approval_step_budget_exceeded" for event in resumed)
