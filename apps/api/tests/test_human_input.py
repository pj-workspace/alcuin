"""Exercise the real pause/answer/continuation boundary with a deterministic provider."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from alcuin_api.config import Settings
from alcuin_api.main import create_app
from alcuin_core.human_input import HumanAnswer, HumanQuestion
from alcuin_operations_copilot import operations_demo_adapter, seed_operations_demo
from support import create_test_store
from test_conversation_context import _wait_for_run

HEADERS = {"X-Alcuin-Workspace": "ws_demo"}


def question_client():
    store = create_test_store()
    seed_operations_demo(store)
    payloads = []

    async def handler(request):
        body = json.loads(request.content)
        # Naming has no tools and is independent of the model-loop requests.
        if not body.get("tools"):
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"Report audience"}}]}\n\ndata: [DONE]\n\n', headers={"content-type": "text/event-stream"})
        payloads.append(body)
        messages = body["messages"]
        tools = [m for m in messages if m["role"] == "tool"]
        if not tools and "follow-up" not in str(messages[-1]["content"]):
            calls = [("read", "ops_search_incidents", {"query": "current incident"})]
        elif len(tools) == 1:
            calls = [("question", "human_ask", {"question": "Who will read the report?", "options": ["Engineering", "Management"]}),
                     ("deferred", "ops_search_incidents", {"query": "must not execute before answer"})]
        else:
            calls = []
        delta = {"tool_calls": [{"index": i, "id": cid, "function": {"name": name, "arguments": json.dumps(args)}} for i, (cid, name, args) in enumerate(calls)]} if calls else {"content": "Report prepared using your answer."}
        return httpx.Response(200, text=f"data: {json.dumps({'choices': [{'delta': delta}]})}\n\ndata: [DONE]\n\n", headers={"content-type": "text/event-stream"})

    app = create_app(Settings(deepseek_api_key="test-provider-key", deepseek_base_url="http://provider.test/v1", deepseek_protocol="chat_completions", openai_api_key=None),
                     store=store, provider_transport=httpx.MockTransport(handler), builtin_adapters={"operations-demo": operations_demo_adapter})
    return TestClient(app), payloads


def start_question(client, attachment_ids=None):
    thread = client.post("/v1/threads", headers=HEADERS, json={"agent_id": "agt_operations"}).json()
    run = client.post(f"/v1/threads/{thread['id']}/runs", headers=HEADERS, json={"input": "Prepare a report", "attachment_ids": attachment_ids or []}).json()
    snapshot = _wait_for_run(client, run["id"], HEADERS, terminal={"waiting_for_input", "failed"})
    assert snapshot["status"] == "waiting_for_input", snapshot
    question = next(e["payload"] for e in snapshot["events"] if e["type"] == "input.required")
    return thread, run, question


@pytest.mark.parametrize("skip", [False, True])
def test_answer_resumes_same_run_without_replaying_tools_and_is_remembered(skip):
    client, payloads = question_client()
    with client:
        thread, run, question = start_question(client)
        url = f"/v1/runs/{run['id']}/inputs/{question['input_id']}"
        body = {"answer": "" if skip else "Engineering, in Chinese", "skip": skip}
        assert len(payloads) == 2
        assert client.post(url, headers={"X-Alcuin-Workspace": "ws_other"}, json=body).status_code == 404
        assert client.post(f"/v1/threads/{thread['id']}/runs", headers=HEADERS, json={"input": "another"}).status_code == 409
        # Pending questions survive worker recovery; no model call is repeated.
        client.app.state.store.recover_interrupted_runs()
        assert client.get(f"/v1/runs/{run['id']}", headers=HEADERS).json()["status"] == "waiting_for_input"
        stream = client.get(f"/v1/runs/{run['id']}/events", headers=HEADERS)
        assert "input.required" in stream.text and "run.completed" not in stream.text
        detail = client.get(f"/v1/threads/{thread['id']}", headers=HEADERS).json()
        assert len(detail["input_events"]) == 1
        assert "continuation" not in json.dumps(detail)
        assert "next_step" not in stream.text
        response = client.post(url, headers=HEADERS, json=body)
        assert response.status_code == 202, response.text
        assert set(response.json()) == {"input_id", "run_id", "status"}
        done = _wait_for_run(client, run["id"], HEADERS)
        assert done["status"] == "completed", done
        assert len(payloads) == 3
        tools = [m for m in payloads[-1]["messages"] if m["role"] == "tool"]
        assert [m["tool_call_id"] for m in tools] == ["read", "deferred", "question"]
        assert json.loads(tools[-2]["content"])["status"] == "deferred"
        assert json.loads(tools[-1]["content"])["skip"] is skip
        assert body["answer"] in tools[-1]["content"]
        assert len([e for e in done["events"] if e["type"] == "run.started"]) == 1
        assert len([e for e in done["events"] if e["type"] == "tool.completed" and e["payload"]["tool"] == "ops.search_incidents"]) == 1
        assert client.post(url, headers=HEADERS, json=body).status_code == 409
        detail = client.get(f"/v1/threads/{thread['id']}", headers=HEADERS).json()
        assert [e["type"] for e in detail["input_events"]] == ["input.required", "input.answered"]
        assert client.app.state.store.list_thread_input_answers("ws_other", thread["id"]) == []
        next_run = client.post(f"/v1/threads/{thread['id']}/runs", headers=HEADERS, json={"input": "follow-up"}).json()
        assert _wait_for_run(client, next_run["id"], HEADERS)["status"] == "completed"
        prior_user = next(m["content"] for m in payloads[-1]["messages"] if m["role"] == "user")
        assert question["input_id"] in prior_user
        assert "Who will read the report?" in prior_user
        assert body["answer"] in prior_user
        # Projection does not rewrite the original user message.
        original = next(m for m in detail["messages"] if m["role"] == "user")
        assert original["parts"] == [{"type": "text", "text": "Prepare a report"}]


def test_question_rejects_secrets_and_answer_claim_is_atomic(monkeypatch):
    client, _ = question_client()
    with client:
        _, run, question = start_question(client)
        url = f"/v1/runs/{run['id']}/inputs/{question['input_id']}"
        assert client.post(url, headers=HEADERS, json={"answer": "sk-" + "a" * 30}).status_code == 422
        store = client.app.state.store
        append = store._append_event_locked
        def fail(*args, **kwargs):
            append(*args, **kwargs)
            raise RuntimeError("injected rollback")
        with monkeypatch.context() as patch:
            patch.setattr(store, "_append_event_locked", fail)
            with pytest.raises(RuntimeError, match="injected"):
                store.answer_run_input("ws_demo", run["id"], question["input_id"], {"answer": "Engineering"})
        assert store.get_run("ws_demo", run["id"])["status"] == "waiting_for_input"
        assert not any(e["type"] == "input.answered" for e in store.list_events("ws_demo", run["id"]))
        record = store.answer_run_input("ws_demo", run["id"], question["input_id"], {"answer": "Engineering"})
        assert record["continuation"]["next_step"] == 2
        assert record["continuation"]["tool_call_counts"] == {"ops.search_incidents": 1, "human.ask": 1}
        store.recover_interrupted_runs()
        assert store.get_run("ws_demo", run["id"])["status"] == "failed"
        assert client.post(url, headers=HEADERS, json={"answer": "Again"}).status_code == 409


def test_answer_validation_never_echoes_invalid_sensitive_input():
    client, _ = question_client()
    with client:
        _, run, question = start_question(client)
        url = f"/v1/runs/{run['id']}/inputs/{question['input_id']}"
        for body in [
            {"answer": "test-provider-key", "skip": True},
            {"answer": "test-provider-key" * 400},
            {"answer": {"password": "test-provider-key"}},
            {"answer": "Yes", "password": "test-provider-key"},
        ]:
            response = client.post(url, headers=HEADERS, json=body)
            assert response.status_code == 422
            assert "test-provider-key" not in response.text
            assert "password" not in response.text
        response = client.post(url, headers=HEADERS, json={"answer": "Avoid test-provider-key"})
        assert response.status_code == 202
        snapshot = _wait_for_run(client, run["id"], HEADERS)
        assert "test-provider-key" not in json.dumps(snapshot)
        assert any(e["type"] == "input.answered" and e["payload"]["answer"] == "Avoid [REDACTED]" for e in snapshot["events"])


@pytest.mark.parametrize("body", [{}, {"answer": " "}, {"answer": "yes", "skip": True}])
def test_answer_requires_text_or_explicit_skip(body):
    with pytest.raises(ValidationError):
        HumanAnswer.model_validate(body)


@pytest.mark.parametrize("body", [{"question": " "}, {"question": "Choose", "options": ["A", "A"]}, {"question": "Choose", "options": [" "]}])
def test_question_contract_rejects_empty_or_ambiguous_choices(body):
    with pytest.raises(ValidationError):
        HumanQuestion.model_validate(body)


def test_question_checkpoint_keeps_image_references_and_resume_revalidates_bytes():
    from test_attachments import upload
    client, payloads = question_client()
    with client:
        resource = upload(client, upload_id="question-image", name="diagram.png", content=b"\x89PNG\r\n\x1a\n", media_type="image/png").json()
        _, run, question = start_question(client, [resource["id"]])
        store = client.app.state.store
        row = store._one("SELECT continuation_json FROM run_inputs WHERE id = ?", (question["input_id"],))
        assert "data:image" not in row["continuation_json"]
        assert f"alcuin-attachment://{resource['id']}" in row["continuation_json"]
        assert "data:image/png;base64," in json.dumps(payloads[0])
        response = client.post(f"/v1/runs/{run['id']}/inputs/{question['input_id']}", headers=HEADERS, json={"answer": "Engineering"})
        assert response.status_code == 202
        assert _wait_for_run(client, run["id"], HEADERS)["status"] == "completed"
        assert "data:image/png;base64," in json.dumps(payloads[-1])
        assert "alcuin-attachment://" not in json.dumps(payloads[-1])
        assert store._one("SELECT continuation_json FROM run_inputs WHERE id = ?", (question["input_id"],))["continuation_json"] is None


def test_interaction_tool_is_host_gated_not_agent_self_authorized():
    from alcuin_api.runtime import RuntimeRequest, _runtime_tool_names
    from alcuin_core.contracts import AgentDefinition
    definition = AgentDefinition(identity={"name": "Test"}, instructions="Help the user", tools=["human.ask"])
    values = dict(workspace_id="ws_demo", run_id="run_test", prompt="Hi", thread_context={}, definition=definition)
    assert "human.ask" not in _runtime_tool_names(RuntimeRequest(**values))
    assert _runtime_tool_names(RuntimeRequest(**values, interactive=True)).count("human.ask") == 1


async def test_resuming_does_not_reset_the_model_step_budget():
    from dataclasses import replace
    from alcuin_api.human_input import human_question_tool
    from alcuin_api.runtime import OpenAICompatibleRuntime, RuntimeRequest
    from alcuin_api.tools import ToolExecutor, ToolRegistry
    from alcuin_core.contracts import AgentDefinition
    requests = []
    async def handler(request):
        requests.append(json.loads(request.content))
        delta = {"tool_calls": [{"index": 0, "id": f"q{len(requests)}", "function": {"name": "human_ask", "arguments": json.dumps({"question": "Clarify?"})}}]}
        return httpx.Response(200, text=f"data: {json.dumps({'choices': [{'delta': delta}]})}\n\ndata: [DONE]\n\n")
    registry = ToolRegistry([human_question_tool()])
    runtime = OpenAICompatibleRuntime(Settings(deepseek_api_key="test", deepseek_protocol="chat_completions"), httpx.MockTransport(handler), tool_executor=ToolExecutor(registry))
    request = RuntimeRequest(workspace_id="ws_test", run_id="run_test", prompt="Help", thread_context={}, interactive=True,
        definition=AgentDefinition(identity={"name": "Test"}, instructions="Help the user", model={"provider": "deepseek", "model": "test-model"}, runtime={"max_steps": 2}))
    first = [event async for event in runtime.stream(request)]
    checkpoint = first[-1].continuation
    assert checkpoint["next_step"] == 1
    checkpoint["messages"].append({"role": "tool", "tool_call_id": "q1", "content": "User answered"})
    resumed = []
    with pytest.raises(RuntimeError, match="max_steps=2"):
        async for event in runtime.stream(replace(request, continuation=checkpoint)):
            resumed.append(event)
    assert len(requests) == 2
    assert all(event.type != "input.required" for event in resumed)
    assert any(event.type == "tool.completed" and event.payload.get("error", {}).get("code") == "invalid_question" for event in resumed)
