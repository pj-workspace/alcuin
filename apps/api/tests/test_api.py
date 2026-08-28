from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from alcuin_api.config import Settings
from alcuin_api.contracts import EmbedClaims
from alcuin_api.main import create_app
from alcuin_api.security import issue_embed_token
from alcuin_api.store import Store


def make_client() -> TestClient:
    store = Store(":memory:")
    app = create_app(
        Settings(
            database_path=":memory:",
            openai_api_key=None,
            deepseek_api_key=None,
            cors_origins="http://localhost:3000",
        ),
        store=store,
    )
    return TestClient(app)


def test_workspace_boundary_hides_resources() -> None:
    with make_client() as client:
        assert client.get("/v1/agents", headers={"X-Alcuin-Workspace": "ws_demo"}).status_code == 200
        response = client.get("/v1/agents/agt_operations", headers={"X-Alcuin-Workspace": "ws_other"})
        assert response.status_code == 404
        assert client.get("/v1/extensions", headers={"X-Alcuin-Workspace": "ws_other"}).json() == []


def test_provider_status_never_returns_credentials() -> None:
    with make_client() as client:
        response = client.get("/v1/providers", headers={"X-Alcuin-Workspace": "ws_demo"})
        assert response.status_code == 200
        deepseek = next(item for item in response.json() if item["id"] == "deepseek")
        assert deepseek["default_model"] == "deepseek-v4-flash-vision-exp"
        assert deepseek["input_modalities"] == ["text", "image"]
        assert "api_key" not in deepseek


def test_run_emits_ordered_terminal_events() -> None:
    with make_client() as client:
        headers = {"X-Alcuin-Workspace": "ws_demo"}
        thread = client.post(
            "/v1/threads",
            headers=headers,
            json={"agent_id": "agt_operations", "title": "Incident review", "context": {}},
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
        assert "event: run.completed" in resumed
        assert "data: [DONE]" in resumed

        tcm_stream = client.get(
            f"/v1/runs/{run['id']}/events?protocol=tcm",
            headers=headers,
        ).text
        tcm_frames = [
            json.loads(line[6:])
            for line in tcm_stream.splitlines()
            if line.startswith("data: {")
        ]
        tcm_types = [frame["type"] for frame in tcm_frames]
        assert tcm_types[0] == "meta"
        assert tcm_types[-1] == "done"
        assert tcm_types.index("tool-call") < tcm_types.index("tool-result")
        assert tcm_types.index("tool-result") < tcm_types.index("text-delta")
        assert "source-registry" in tcm_types
        assert "artifact-updated" in tcm_types


def test_mutating_tool_waits_for_approval() -> None:
    with make_client() as client:
        headers = {"X-Alcuin-Workspace": "ws_demo"}
        thread = client.post(
            "/v1/threads",
            headers=headers,
            json={"agent_id": "agt_operations", "context": {"record": {"id": "INC-222"}}},
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
        approval_event = next(event for event in body["events"] if event["type"] == "approval.required")
        approval_id = approval_event["payload"]["approval_id"]
        decision = client.post(
            f"/v1/runs/{run['id']}/approvals/{approval_id}",
            headers=headers,
            json={"decision": "approved"},
        )
        assert decision.status_code == 200
        final = client.get(f"/v1/runs/{run['id']}", headers=headers).json()
        assert final["status"] == "completed"
        assert final["events"][-1]["type"] == "run.completed"


def test_embed_token_is_agent_and_origin_bound() -> None:
    with make_client() as client:
        session = client.post(
            "/v1/embed/sessions",
            headers={"X-Alcuin-Workspace": "ws_demo"},
            json={"agent_id": "agt_operations", "origin": "http://localhost:3000"},
        ).json()
        bearer = {"Authorization": f"Bearer {session['token']}", "Origin": "http://localhost:3000"}
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
            headers={"Authorization": f"Bearer {restricted}", "Origin": "http://localhost:3000"},
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
            headers={"Authorization": f"Bearer {expired}", "Origin": "http://localhost:3000"},
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
        assert client.post(
            "/v1/agents/agt_operations/versions",
            headers=operator,
            json={"definition": definition},
        ).status_code == 201
        assert client.post("/v1/agents/agt_operations/publish", headers=operator).status_code == 200

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
