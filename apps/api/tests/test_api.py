from __future__ import annotations

import json
import time

import httpx
from fastapi.testclient import TestClient

from alcuin_api.config import Settings
from alcuin_api.contracts import EmbedClaims
from alcuin_api.main import create_app
from alcuin_api.security import issue_embed_token
from alcuin_api.store import Store
from alcuin_extensions.operations_toolkit import operations_demo_adapter


def fake_provider_transport() -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        messages = payload.get("messages") or []
        last_message = messages[-1] if messages else {}
        if last_message.get("role") == "tool":
            content = (
                "I reviewed the current record for INC-104. Checkout latency is recovering, "
                "the mitigation is active, and no new payment failures have appeared in the last 20 minutes."
            )
            event = {"choices": [{"delta": {"content": content}}]}
        else:
            prompt = str(last_message.get("content") or "").casefold()
            mutating = any(
                term in prompt for term in ("update", "change", "修改", "更新")
            )
            name = "ops_update_ticket" if mutating else "ops_search_incidents"
            arguments = (
                {"ticket_id": "INC-104", "status": "monitoring"}
                if mutating
                else {"query": prompt[:120]}
            )
            event = {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": f"call_{name}",
                                    "function": {
                                        "name": name,
                                        "arguments": json.dumps(arguments),
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        body = f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n"
        return httpx.Response(
            200, text=body, headers={"content-type": "text/event-stream"}
        )

    return httpx.MockTransport(handler)


def make_client(
    *,
    searxng_url: str = "http://searx.test",
    operations_adapter: bool = True,
) -> TestClient:
    store = Store(":memory:")
    app = create_app(
        Settings(
            database_path=":memory:",
            openai_api_key=None,
            deepseek_api_key="test-provider-key",
            deepseek_base_url="http://provider.test/v1",
            deepseek_protocol="chat_completions",
            searxng_url=searxng_url,
            cors_origins="http://localhost:3000",
        ),
        store=store,
        builtin_adapters=(
            {"operations-demo": operations_demo_adapter} if operations_adapter else None
        ),
        provider_transport=fake_provider_transport(),
    )
    return TestClient(app)


def make_client_with_operations_adapter() -> TestClient:
    return make_client(operations_adapter=True)


def test_workspace_boundary_hides_resources() -> None:
    with make_client() as client:
        assert (
            client.get(
                "/v1/agents", headers={"X-Alcuin-Workspace": "ws_demo"}
            ).status_code
            == 200
        )
        response = client.get(
            "/v1/agents/agt_operations", headers={"X-Alcuin-Workspace": "ws_other"}
        )
        assert response.status_code == 404
        assert (
            client.get(
                "/v1/extensions", headers={"X-Alcuin-Workspace": "ws_other"}
            ).json()
            == []
        )


def test_provider_status_never_returns_credentials() -> None:
    with make_client() as client:
        response = client.get(
            "/v1/providers", headers={"X-Alcuin-Workspace": "ws_demo"}
        )
        assert response.status_code == 200
        deepseek = next(item for item in response.json() if item["id"] == "deepseek")
        assert deepseek["default_model"] == "deepseek-v4-flash-vision-exp"
        assert deepseek["input_modalities"] == ["text", "image"]
        assert "api_key" not in deepseek


def test_agent_creation_is_versioned_and_rejects_duplicate_workspace_slug() -> None:
    definition = {
        "identity": {
            "name": "Release Copilot",
            "description": "Coordinates release readiness.",
            "icon": "spark",
        },
        "instructions": "Coordinate release readiness using only explicitly bound capabilities.",
        "model": {
            "provider": "deepseek",
            "model": "deepseek-v4-flash-vision-exp",
            "credential_ref": "secret://workspace/deepseek-primary",
        },
        "extensions": [],
        "tools": [],
        "knowledge": [],
        "runtime": {"adapter": "langgraph-react", "max_steps": 8},
        "policies": {"mutating_tools": "ask", "external_side_effects": "ask"},
        "context_policy": {"accepted": ["page", "record"], "max_bytes": 16_384},
        "output_schema": {"type": "artifact", "format": "markdown"},
        "starter_prompts": [],
    }
    headers = {"X-Alcuin-Workspace": "ws_demo"}
    with make_client() as client:
        created = client.post(
            "/v1/agents",
            headers=headers,
            json={"slug": "release-copilot", "definition": definition},
        )
        assert created.status_code == 201
        assert created.json()["status"] == "draft"
        assert created.json()["version"] == 1
        assert created.json()["definition"]["identity"]["name"] == "Release Copilot"

        duplicate = client.post(
            "/v1/agents",
            headers=headers,
            json={"slug": "release-copilot", "definition": definition},
        )
        assert duplicate.status_code == 409
        assert (
            duplicate.json()["detail"] == "Agent slug already exists in this workspace"
        )
        assert (
            len(
                [
                    agent
                    for agent in client.get("/v1/agents", headers=headers).json()
                    if agent["slug"] == "release-copilot"
                ]
            )
            == 1
        )


def test_web_search_tool_is_registered_only_when_searxng_is_configured() -> None:
    store = Store(":memory:")
    app = create_app(
        Settings(database_path=":memory:", searxng_url="http://searx.test"),
        store=store,
    )
    with TestClient(app):
        runtime = app.state.runtime.provider_runtime
        schemas = runtime.tool_executor.provider_schemas(["web.search"])
        assert schemas[0]["function"]["name"] == "web_search"
        assert schemas[0]["function"]["parameters"]["properties"]["depth"]["enum"] == [
            "quick",
            "deep",
        ]

    with make_client(searxng_url="", operations_adapter=False) as client:
        runtime = client.app.state.runtime.provider_runtime
        assert runtime.tool_executor.provider_schemas(["web.search"]) == []


def test_missing_provider_uses_domain_neutral_preview_without_inventing_tool_results() -> (
    None
):
    store = Store(":memory:")
    app = create_app(
        Settings(
            database_path=":memory:",
            deepseek_api_key=None,
            openai_api_key=None,
            searxng_url="http://searx.test",
        ),
        store=store,
        builtin_adapters={"operations-demo": operations_demo_adapter},
    )
    headers = {"X-Alcuin-Workspace": "ws_demo"}
    with TestClient(app) as client:
        thread = client.post(
            "/v1/threads",
            headers=headers,
            json={
                "agent_id": "agt_operations",
                "context": {"record": {"id": "INC-104"}},
            },
        ).json()
        run = client.post(
            f"/v1/threads/{thread['id']}/runs",
            headers=headers,
            json={"input": "Update this incident"},
        ).json()
        deadline = time.time() + 2
        body = None
        while time.time() < deadline:
            body = client.get(f"/v1/runs/{run['id']}", headers=headers).json()
            if body["status"] == "completed":
                break
            time.sleep(0.02)
        assert body is not None
        assert body["status"] == "completed"
        assert not any(event["type"].startswith("tool.") for event in body["events"])
        message = "".join(
            event["payload"].get("delta", "")
            for event in body["events"]
            if event["type"] == "message.delta"
        )
        assert "no model credential is configured" in message
        assert "INC-104" not in message


def test_workspace_tool_catalog_reports_authoritative_runtime_availability() -> None:
    headers = {"X-Alcuin-Workspace": "ws_demo"}
    with make_client_with_operations_adapter() as client:
        response = client.get("/v1/tools", headers=headers)
        assert response.status_code == 200
        catalog = {tool["id"]: tool for tool in response.json()}
        assert catalog["web.search"] == {
            "id": "web.search",
            "name": "web.search",
            "description": catalog["web.search"]["description"],
            "source": "builtin",
            "extension_manifest_id": None,
            "extension_name": None,
            "mutating": False,
            "available": True,
            "status": "available",
        }
        assert catalog["ops.search_incidents"]["source"] == "extension"
        assert catalog["ops.search_incidents"]["extension_manifest_id"] == "ops-toolkit"
        assert catalog["ops.search_incidents"]["available"] is True
        assert catalog["ops.update_ticket"]["mutating"] is True
        assert (
            client.get("/v1/bootstrap", headers=headers).json()["tools"]
            == response.json()
        )

    with make_client(searxng_url="", operations_adapter=False) as client:
        catalog = {
            tool["id"]: tool for tool in client.get("/v1/tools", headers=headers).json()
        }
        assert "web.search" not in catalog
        assert catalog["ops.search_incidents"]["status"] == "adapter_missing"
        runtime = client.app.state.runtime.provider_runtime
        assert runtime.tool_executor.provider_schemas(
            ["ops.search_incidents"], workspace_id="ws_demo"
        ) == []
        publish = client.post("/v1/agents/agt_operations/publish", headers=headers)
        assert publish.status_code == 409
        assert "ops.search_incidents" in publish.json()["detail"]


def test_agent_tool_references_are_validated_and_web_search_requires_configuration() -> (
    None
):
    headers = {"X-Alcuin-Workspace": "ws_demo"}
    with make_client(searxng_url="") as client:
        definition = client.get("/v1/agents/agt_operations", headers=headers).json()[
            "definition"
        ]
        definition["extensions"] = []
        definition["tools"] = ["unknown.tool"]
        unknown = client.post(
            "/v1/agents",
            headers=headers,
            json={"slug": "unknown-tool-agent", "definition": definition},
        )
        assert unknown.status_code == 422
        assert (
            unknown.json()["detail"]
            == "Agent references unavailable tools: unknown.tool"
        )

        definition["tools"] = ["ops.search_incidents"]
        unbound = client.post(
            "/v1/agents",
            headers=headers,
            json={"slug": "unbound-extension-agent", "definition": definition},
        )
        assert unbound.status_code == 422
        assert "Bind the contributing extension" in unbound.json()["detail"]

        definition["tools"] = ["web.search"]
        created = client.post(
            "/v1/agents",
            headers=headers,
            json={"slug": "web-agent", "definition": definition},
        )
        assert created.status_code == 201
        publish = client.post(
            f"/v1/agents/{created.json()['id']}/publish", headers=headers
        )
        assert publish.status_code == 409
        assert (
            publish.json()["detail"]
            == "Configure web search before publishing web.search"
        )


def test_run_emits_ordered_terminal_events() -> None:
    with make_client() as client:
        headers = {"X-Alcuin-Workspace": "ws_demo"}
        thread = client.post(
            "/v1/threads",
            headers=headers,
            json={
                "agent_id": "agt_operations",
                "title": "Incident review",
                "context": {},
            },
        ).json()
        run = client.post(
            f"/v1/threads/{thread['id']}/runs",
            headers=headers,
            json={"input": "Summarize the checkout incident"},
        ).json()
        deadline = time.time() + 3
        body = None
        while time.time() < deadline:
            body = client.get(f"/v1/runs/{run['id']}", headers=headers).json()
            if body["status"] == "completed":
                break
            time.sleep(0.03)
        assert body is not None
        assert body["status"] == "completed"
        sequences = [event["sequence"] for event in body["events"]]
        assert sequences == sorted(sequences)
        assert body["events"][0]["type"] == "run.started"
        assert body["events"][-1]["type"] == "run.completed"
        resumed = client.get(
            f"/v1/runs/{run['id']}/events?after=2",
            headers=headers,
        ).text
        assert "id: 1\n" not in resumed
        assert "id: 2\n" not in resumed
        assert "event: run.completed" in resumed
        assert "data: [DONE]" in resumed

        resumed_by_header = client.get(
            f"/v1/runs/{run['id']}/events?after=1",
            headers={**headers, "Last-Event-ID": "2"},
        ).text
        resumed_ids = [
            int(line[4:])
            for line in resumed_by_header.splitlines()
            if line.startswith("id: ")
        ]
        assert "id: 1\n" not in resumed_by_header
        assert "id: 2\n" not in resumed_by_header
        assert [1, 2, *resumed_ids] == sequences
        assert len(set(resumed_ids)) == len(resumed_ids)
        assert "event: run.completed" in resumed_by_header

        invalid_cursor = client.get(
            f"/v1/runs/{run['id']}/events",
            headers={**headers, "Last-Event-ID": "not-a-sequence"},
        )
        assert invalid_cursor.status_code == 400
        assert invalid_cursor.json()["detail"] == (
            "Last-Event-ID must be a non-negative event sequence"
        )

        tcm_stream = client.get(
            f"/v1/runs/{run['id']}/events?protocol=tcm",
            headers=headers,
        ).text
        tcm_frames = [
            json.loads(line[6:])
            for line in tcm_stream.splitlines()
            if line.startswith("data: {")
        ]
        tcm_ids = [
            int(line[4:]) for line in tcm_stream.splitlines() if line.startswith("id: ")
        ]
        tcm_types = [frame["type"] for frame in tcm_frames]
        assert tcm_ids == [frame["sequence"] for frame in tcm_frames]
        assert tcm_ids == sorted(set(tcm_ids))
        assert tcm_types[0] == "meta"
        assert tcm_types[-1] == "done"
        assert tcm_types.index("tool-call") < tcm_types.index("tool-result")
        assert tcm_types.index("tool-result") < tcm_types.index("text-delta")
        assert "source-registry" in tcm_types
        assert "artifact-updated" in tcm_types


def test_approved_tool_fails_truthfully_when_runtime_handler_is_unavailable() -> None:
    with make_client(operations_adapter=True) as client:
        headers = {"X-Alcuin-Workspace": "ws_demo"}
        thread = client.post(
            "/v1/threads",
            headers=headers,
            json={
                "agent_id": "agt_operations",
                "context": {"record": {"id": "INC-222"}},
            },
        ).json()
        run = client.post(
            f"/v1/threads/{thread['id']}/runs",
            headers=headers,
            json={"input": "Update this incident to monitoring"},
        ).json()
        deadline = time.time() + 2
        body = None
        while time.time() < deadline:
            body = client.get(f"/v1/runs/{run['id']}", headers=headers).json()
            if body["status"] == "waiting_for_approval":
                break
            time.sleep(0.02)
        approval_event = next(
            event for event in body["events"] if event["type"] == "approval.required"
        )
        approval_id = approval_event["payload"]["approval_id"]
        client.app.state.extension_tool_service.builtin_adapters.clear()
        decision = client.post(
            f"/v1/runs/{run['id']}/approvals/{approval_id}",
            headers=headers,
            json={"decision": "approved"},
        )
        assert decision.status_code == 200
        final = client.get(f"/v1/runs/{run['id']}", headers=headers).json()
        assert final["status"] == "failed"
        assert final["events"][-1]["type"] == "run.failed"
        assert final["events"][-2]["type"] == "tool.completed"
        assert (
            final["events"][-2]["payload"]["error"]["code"] == "extension_unavailable"
        )


def test_declarative_ui_tool_runs_validate_forms_and_gate_mutations() -> None:
    with make_client_with_operations_adapter() as client:
        headers = {"X-Alcuin-Workspace": "ws_demo"}
        thread = client.post(
            "/v1/threads",
            headers=headers,
            json={
                "agent_id": "agt_operations",
                "context": {"record": {"id": "INC-UI-1"}},
            },
        ).json()

        forged = client.post(
            f"/v1/threads/{thread['id']}/runs",
            headers=headers,
            json={
                "input": "Forged declarative extension action",
                "requested_tool": {
                    "name": "ops.update_ticket",
                    "arguments": {"ticket_id": "INC-UI-1", "status": "resolved"},
                    "extension_manifest_id": "ops-toolkit",
                    "ui_block_id": "incident-summary",
                },
            },
        )
        assert forged.status_code == 409
        assert forged.json()["detail"] == "UI form is unavailable"

        write_run = client.post(
            f"/v1/threads/{thread['id']}/runs",
            headers=headers,
            json={
                "input": "Update incident · declarative extension action",
                "requested_tool": {
                    "name": "ops.update_ticket",
                    "arguments": {"ticket_id": "INC-UI-1", "status": "resolved"},
                    "extension_manifest_id": "ops-toolkit",
                    "ui_block_id": "update-incident",
                },
            },
        ).json()
        deadline = time.time() + 2
        write_body = None
        while time.time() < deadline:
            write_body = client.get(
                f"/v1/runs/{write_run['id']}", headers=headers
            ).json()
            if write_body["status"] == "waiting_for_approval":
                break
            time.sleep(0.02)
        assert write_body is not None
        assert write_body["status"] == "waiting_for_approval"
        approval_event = next(
            event
            for event in write_body["events"]
            if event["type"] == "approval.required"
        )
        assert approval_event["payload"]["source"] == "extension.ui_block"
        assert approval_event["payload"]["ui_block_id"] == "update-incident"
        assert not any(
            event["type"] == "tool.completed" for event in write_body["events"]
        )

        decision = client.post(
            f"/v1/runs/{write_run['id']}/approvals/{approval_event['payload']['approval_id']}",
            headers=headers,
            json={"decision": "approved"},
        )
        assert decision.status_code == 200
        final = client.get(f"/v1/runs/{write_run['id']}", headers=headers).json()
        assert final["status"] == "completed"
        completed = next(
            event for event in final["events"] if event["type"] == "tool.completed"
        )
        assert completed["payload"]["result"] == {
            "ticket_id": "INC-UI-1",
            "status": "resolved",
            "updated": True,
        }


def test_embed_token_is_agent_and_origin_bound() -> None:
    with make_client() as client:
        session = client.post(
            "/v1/embed/sessions",
            headers={"X-Alcuin-Workspace": "ws_demo"},
            json={"agent_id": "agt_operations", "origin": "http://localhost:3000"},
        ).json()
        bearer = {
            "Authorization": f"Bearer {session['token']}",
            "Origin": "http://localhost:3000",
        }
        allowed = client.post(
            "/v1/threads",
            headers=bearer,
            json={"agent_id": "agt_operations", "context": {}},
        )
        assert allowed.status_code == 201
        denied = client.post(
            "/v1/threads",
            headers={**bearer, "Origin": "https://evil.example"},
            json={"agent_id": "agt_operations", "context": {}},
        )
        assert denied.status_code == 403
        management = client.get("/v1/agents", headers=bearer)
        assert management.status_code == 403


def test_embed_token_rejects_disallowed_action_and_expiry() -> None:
    with make_client() as client:
        now = int(time.time())
        restricted = issue_embed_token(
            EmbedClaims(
                workspace_id="ws_demo",
                agent_id="agt_operations",
                agent_version_id="agv_operations_v1",
                origin="http://localhost:3000",
                allowed_actions=["thread:create"],
                issued_at=now,
                expires_at=now + 60,
            )
        )
        response = client.get(
            "/v1/runs/run_demo",
            headers={
                "Authorization": f"Bearer {restricted}",
                "Origin": "http://localhost:3000",
            },
        )
        assert response.status_code == 403

        expired = issue_embed_token(
            EmbedClaims(
                workspace_id="ws_demo",
                agent_id="agt_operations",
                agent_version_id="agv_operations_v1",
                origin="http://localhost:3000",
                allowed_actions=["run:read"],
                issued_at=now - 120,
                expires_at=now - 60,
            )
        )
        response = client.get(
            "/v1/runs/run_demo",
            headers={
                "Authorization": f"Bearer {expired}",
                "Origin": "http://localhost:3000",
            },
        )
        assert response.status_code == 401


def test_embed_session_remains_pinned_to_published_agent_version() -> None:
    with make_client() as client:
        operator = {"X-Alcuin-Workspace": "ws_demo"}
        session = client.post(
            "/v1/embed/sessions",
            headers=operator,
            json={"agent_id": "agt_operations", "origin": "http://localhost:3000"},
        ).json()
        original_version = session["agent_version_id"]
        agent = client.get("/v1/agents/agt_operations", headers=operator).json()
        definition = agent["definition"]
        definition["instructions"] = "A newer published instruction set."
        assert (
            client.post(
                "/v1/agents/agt_operations/versions",
                headers=operator,
                json={"definition": definition},
            ).status_code
            == 201
        )
        assert (
            client.post(
                "/v1/agents/agt_operations/publish", headers=operator
            ).status_code
            == 200
        )

        embed = {
            "Authorization": f"Bearer {session['token']}",
            "Origin": "http://localhost:3000",
        }
        thread = client.post(
            "/v1/threads",
            headers=embed,
            json={"agent_id": "agt_operations", "context": {}},
        ).json()
        run = client.post(
            f"/v1/threads/{thread['id']}/runs",
            headers=embed,
            json={"input": "Summarize the incident"},
        ).json()
        assert run["agent_version_id"] == original_version
