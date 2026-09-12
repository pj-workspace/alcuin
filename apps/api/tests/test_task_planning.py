"""Ephemeral planning uses the real adapter with a read-only repository fixture."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alcuin_api.config import Settings
from alcuin_api.security import RequestScope, resolve_scope
from alcuin_api.tasks import TaskPlanner, TaskService, create_task_router


class PlanningRepository:
    # No persistence methods: an accidental write fails the fixture.
    def get_thread(self, workspace_id, thread_id):
        if (workspace_id, thread_id) == ("ws_one", "thread_one"):
            return {"id": thread_id, "agent_version_id": "version_one", "context": {}}
        return None

    def get_agent_version(self, workspace_id, version_id):
        assert workspace_id == "ws_one" and version_id == "version_one"
        return {
            "definition": {
                "identity": {"name": "Research helper"},
                "instructions": "Describe evidence clearly and verify the final deliverable.",
                "model": {"provider": "deepseek", "model": "deepseek-v4-pro"},
                "tools": ["unavailable.secret.tool"],
            }
        }

    def get_thread_configuration(self, workspace_id, thread_id):
        assert workspace_id == "ws_one"
        return {}

    def get_agent_version_customization_bindings(self, workspace_id, version_id):
        assert workspace_id == "ws_one"
        return {}

    def get_workspace_preferences(self, workspace_id):
        assert workspace_id == "ws_one"
        return {"content": "Use concise Chinese descriptions.", "revision": 1}

    def list_rules(self, workspace_id, **kwargs):
        assert workspace_id == "ws_one"
        return []


def client_for(handler, *, key="test-key", scope=None):
    settings = Settings(_env_file=None, deepseek_api_key=key, openai_api_key="")
    repository = PlanningRepository()
    planner = TaskPlanner(repository, settings, transport=httpx.MockTransport(handler))
    service = TaskService(repository, settings=settings, planner=planner)
    app = FastAPI()
    app.dependency_overrides[resolve_scope] = lambda: scope or RequestScope("ws_one")
    app.include_router(create_task_router(service))
    return TestClient(app)


def stream_response(content, *, finish="stop", delta=None):
    events = [
        {"choices": [{"delta": delta or {"content": content}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": finish}]},
    ]
    return httpx.Response(
        200,
        text="".join(f"data: {json.dumps(event)}\n\n" for event in events)
        + "data: [DONE]\n\n",
    )


URL = "/v1/threads/thread_one/task-plan-proposals"
PLAN = {
    "steps": [
        {"title": "核对资料", "description": "整理证据与缺失项"},
        {"title": "撰写总结", "description": "形成可检查的交付内容"},
    ]
}


def test_proposal_reuses_profile_and_returns_only_editable_steps():
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert "tools" not in payload
        assert payload["max_tokens"] == 4096
        assert payload["model"] == "deepseek-v4-flash"
        assert payload["reasoning_effort"] == "low"
        system = payload["messages"][0]["content"]
        assert "verify the final deliverable" in system
        assert "Use concise Chinese descriptions" in system
        assert "unavailable.secret.tool" not in system
        assert "Available tools: none" in system
        return stream_response(json.dumps(PLAN))

    with client_for(handler) as client:
        response = client.post(
            URL,
            json={
                "goal": " 总结资料 ",
                "model_override": "deepseek-v4-flash",
                "reasoning_effort": "low",
            },
        )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "goal": "总结资料",
        **PLAN,
        "model": "deepseek-v4-flash",
        "reasoning_effort": "low",
    }
    assert len(requests) == 1


@pytest.mark.parametrize(
    "content",
    [
        "not JSON",
        '{"steps": []}',
        json.dumps({"steps": [{"title": "x"}] * 9}),
        json.dumps({"steps": [{"title": " "}]}),
        json.dumps({"steps": [{"title": "x", "description": "x" * 2001}]}),
        json.dumps({**PLAN, "reasoning": "private"}),
        "x" * 24_001,
    ],
)
def test_invalid_output_is_rejected_without_raw_provider_text(content):
    with client_for(lambda _: stream_response(content)) as client:
        response = client.post(URL, json={"goal": "Make a plan"})
    assert response.status_code == 502
    assert content not in response.text


def test_provider_truncation_is_not_a_valid_plan():
    with client_for(
        lambda _: stream_response(json.dumps(PLAN), finish="length")
    ) as client:
        response = client.post(URL, json={"goal": "Make a plan"})
    assert response.status_code == 502


def test_unrequested_tool_call_is_rejected_without_execution():
    delta = {
        "tool_calls": [
            {
                "index": 0,
                "id": "call1",
                "function": {"name": "dangerous_tool", "arguments": "{}"},
            }
        ]
    }
    with client_for(
        lambda _: stream_response("", finish="tool_calls", delta=delta)
    ) as client:
        response = client.post(URL, json={"goal": "Make a plan"})
    assert response.status_code == 502


@pytest.mark.parametrize(
    "scope,status",
    [
        (RequestScope("ws_other"), 404),
        (RequestScope("ws_one", embed=True), 403),
        (RequestScope("ws_one", allowed_actions=frozenset({"run:read"})), 403),
    ],
)
def test_scope_checked_before_model_access(scope, status):
    def handler(_):
        pytest.fail("Unauthorized provider access")

    with client_for(handler, scope=scope) as client:
        response = client.post(URL, json={"goal": "Make a plan"})
    assert response.status_code == status


def test_preview_cannot_manufacture_a_plan():
    def handler(_):
        pytest.fail("No-key preview called provider")

    with client_for(handler, key="") as client:
        response = client.post(URL, json={"goal": "Make a plan"})
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [
        {"goal": " "},
        {"goal": "x", "model_override": "unknown"},
        {"goal": "x", "reasoning_effort": "ultra"},
        {"goal": "x", "steps": [{"title": "injected"}]},
    ],
)
def test_invalid_request_does_not_call_provider(body):
    def handler(_):
        pytest.fail("Invalid request called provider")

    with client_for(handler) as client:
        response = client.post(URL, json=body)
    assert response.status_code == 422


def test_timeout_is_bounded_and_errors_are_not_exposed(monkeypatch):
    monkeypatch.setattr("alcuin_api.tasks.planning.PLANNING_TIMEOUT_SECONDS", 0.01)

    async def slow(_):
        await asyncio.sleep(1)
        return stream_response(json.dumps(PLAN))

    with client_for(slow) as client:
        response = client.post(URL, json={"goal": "Make a plan"})
    assert response.status_code == 504
    with client_for(
        lambda _: httpx.Response(500, text="secret-provider-error")
    ) as client:
        response = client.post(URL, json={"goal": "Make a plan"})
    assert response.status_code == 502
    assert "secret-provider-error" not in response.text


def test_responses_protocol_plan_ignores_reasoning(monkeypatch):
    original = Settings.provider

    def provider(settings, provider_id):
        return replace(
            original(settings, provider_id), id="generic", protocol="responses"
        )

    monkeypatch.setattr(Settings, "provider", provider)

    def handler(request):
        payload = json.loads(request.content)
        assert request.url.path.endswith("/responses")
        assert "tools" not in payload
        assert payload["max_output_tokens"] == 4096
        events = [
            {
                "type": "response.reasoning_summary_text.delta",
                "delta": "private deliberation",
            },
            {"type": "response.output_text.delta", "delta": json.dumps(PLAN)},
            {"type": "response.completed", "response": {"status": "completed"}},
        ]
        return httpx.Response(
            200, text="".join(f"data: {json.dumps(event)}\n\n" for event in events)
        )

    with client_for(handler) as client:
        response = client.post(URL, json={"goal": "Make a plan"})
    assert response.status_code == 200, response.text
    assert response.json()["steps"] == PLAN["steps"]
    assert "private" not in response.text


def test_workspace_rules_are_resolved_in_planning_context(monkeypatch):
    def list_rules(self, workspace_id, **kwargs):
        assert workspace_id == "ws_one"
        return (
            [{"id": "rule1", "current_version_id": "rv1"}]
            if kwargs["scope"] == "workspace"
            else []
        )

    def get_rule_version(self, workspace_id, version_id):
        assert workspace_id == "ws_one" and version_id == "rv1"
        return {
            "id": "rv1",
            "definition": {
                "name": "Evidence",
                "activation": "always",
                "content": "Identify unverifiable assumptions in the plan.",
            },
        }

    monkeypatch.setattr(PlanningRepository, "list_rules", list_rules)
    monkeypatch.setattr(
        PlanningRepository, "get_rule_version", get_rule_version, raising=False
    )

    def handler(request):
        assert (
            "Identify unverifiable assumptions"
            in json.loads(request.content)["messages"][0]["content"]
        )
        return stream_response(json.dumps(PLAN))

    with client_for(handler) as client:
        response = client.post(URL, json={"goal": "Make a plan"})
    assert response.status_code == 200, response.text
