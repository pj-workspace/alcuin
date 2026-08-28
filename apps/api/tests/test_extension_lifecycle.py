from __future__ import annotations

import sys
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from alcuin_api.config import Settings
from alcuin_api.mcp_gateway import MCPGateway
from alcuin_api.main import create_app
from alcuin_api.openapi_gateway import OpenAPIGateway
from alcuin_storage import SqliteStore as Store


HEADERS = {"X-Alcuin-Workspace": "ws_demo"}


def lifecycle_client(
    *,
    openapi_transport: httpx.AsyncBaseTransport | None = None,
) -> TestClient:
    store = Store(":memory:")
    settings = Settings(
        database_path=":memory:",
        searxng_url="",
        qdrant_url="",
        dashscope_api_key="",
        deepseek_api_key="",
        openai_api_key="",
        extension_allow_private_networks=True,
    )
    return TestClient(
        create_app(
            settings,
            store=store,
            mcp_gateway=MCPGateway(),
            openapi_gateway=OpenAPIGateway(openapi_transport, timeout_seconds=2),
        )
    )


def test_stdio_mcp_completes_disabled_first_lifecycle_and_call() -> None:
    server = Path(__file__).parent / "fixtures" / "echo_mcp.py"
    payload = {
        "name": "Echo MCP",
        "extension_id": "verification.echo-mcp",
        "description": "Read-only MCP lifecycle verification",
        "entrypoint": {
            "type": "mcp",
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(server)],
        },
        "selected_tools": ["echo"],
    }

    with lifecycle_client() as client:
        inspected = client.post(
            "/v1/extensions/import/mcp",
            headers=HEADERS,
            json=payload,
        )
        assert inspected.status_code == 200
        inspection = inspected.json()
        assert inspection["install_state"] == "ready_for_disabled_install"
        assert inspection["manifest"]["contributions"]["tools"][0]["name"] == "echo"
        assert inspection["manifest"]["contributions"]["tools"][0]["mutating"] is False
        assert inspection["permission_summary"]["requires_review"] is False

        installed = client.post(
            "/v1/extensions",
            headers=HEADERS,
            json={"manifest": inspection["manifest"]},
        )
        assert installed.status_code == 201
        extension = installed.json()
        assert extension["status"] == "disabled"
        assert extension["health"] == "unchecked"

        premature_enable = client.patch(
            f"/v1/extensions/{extension['id']}",
            headers=HEADERS,
            json={"enabled": True},
        )
        assert premature_enable.status_code == 409

        health = client.post(
            f"/v1/extensions/{extension['id']}/health",
            headers=HEADERS,
        )
        assert health.status_code == 200
        assert health.json()["status"] == "healthy"
        assert {tool["name"] for tool in health.json()["details"]["tools"]} == {
            "echo",
            "write_marker",
        }
        refreshed = client.get("/v1/extensions", headers=HEADERS).json()
        refreshed_extension = next(item for item in refreshed if item["id"] == extension["id"])
        assert [
            tool["name"]
            for tool in refreshed_extension["manifest"]["contributions"]["tools"]
        ] == ["echo"]

        enabled = client.patch(
            f"/v1/extensions/{extension['id']}",
            headers=HEADERS,
            json={"enabled": True},
        )
        assert enabled.status_code == 200
        assert enabled.json()["status"] == "enabled"

        called = client.post(
            f"/v1/extensions/{extension['id']}/tools/echo:call",
            headers=HEADERS,
            json={"arguments": {"message": "hello lifecycle"}},
        )
        assert called.status_code == 200
        assert called.json()["structured_content"] == {"message": "hello lifecycle"}

        undeclared = client.post(
            f"/v1/extensions/{extension['id']}/tools/not-reviewed:call",
            headers=HEADERS,
            json={"arguments": {}},
        )
        assert undeclared.status_code == 404

        agent = client.get("/v1/agents", headers=HEADERS).json()[0]
        definition = agent["definition"]
        definition["tools"] = [
            *definition["tools"],
            "extension.verification.echo-mcp.echo",
        ]
        missing_binding = client.post(
            f"/v1/agents/{agent['id']}/versions",
            headers=HEADERS,
            json={"definition": definition},
        )
        assert missing_binding.status_code == 422

        definition["extensions"] = [
            *definition["extensions"],
            "verification.echo-mcp",
        ]
        saved = client.post(
            f"/v1/agents/{agent['id']}/versions",
            headers=HEADERS,
            json={"definition": definition},
        )
        assert saved.status_code == 201

        disabled = client.patch(
            f"/v1/extensions/{extension['id']}",
            headers=HEADERS,
            json={"enabled": False},
        )
        assert disabled.status_code == 200
        blocked_publish = client.post(
            f"/v1/agents/{agent['id']}/publish",
            headers=HEADERS,
        )
        assert blocked_publish.status_code == 409


def test_openapi_lifecycle_requires_resolved_secret_and_real_health(
    monkeypatch,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer lifecycle-token"
        if request.method == "HEAD":
            assert str(request.url) == "http://127.0.0.1:9411/v1"
            return httpx.Response(204)
        assert request.method == "GET"
        assert str(request.url) == "http://127.0.0.1:9411/v1/records/R-42?verbose=true"
        return httpx.Response(200, json={"id": "R-42", "status": "verified"})

    specification = {
        "openapi": "3.1.0",
        "info": {"title": "Records", "version": "1.0.0"},
        "servers": [{"url": "http://127.0.0.1:9411/v1"}],
        "paths": {
            "/records/{id}": {
                "get": {
                    "operationId": "getRecord",
                    "summary": "Get a record",
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "verbose", "in": "query", "schema": {"type": "boolean"}},
                    ],
                }
            }
        },
    }

    with lifecycle_client(openapi_transport=httpx.MockTransport(handler)) as client:
        inspected = client.post(
            "/v1/extensions/import/openapi",
            headers=HEADERS,
            json={
                "name": "Records API",
                "extension_id": "verification.records-api",
                "spec_text": __import__("json").dumps(specification),
                "auth": "bearer",
            },
        )
        assert inspected.status_code == 200
        inspection = inspected.json()
        tool = inspection["manifest"]["contributions"]["tools"][0]
        assert tool["input_schema"]["required"] == ["id"]
        assert set(tool["input_schema"]["properties"]) == {"id", "verbose"}

        installed = client.post(
            "/v1/extensions",
            headers=HEADERS,
            json={"manifest": inspection["manifest"]},
        ).json()
        assert installed["status"] == "disabled"

        missing = client.post(
            f"/v1/extensions/{installed['id']}/health",
            headers=HEADERS,
        ).json()
        assert missing["status"] == "unhealthy"
        assert missing["details"]["missing_credentials"] == ["api-credential"]

        unknown = client.patch(
            f"/v1/extensions/{installed['id']}/credentials",
            headers=HEADERS,
            json={"credential_refs": {"wrong": "secret://workspace/lifecycle"}},
        )
        assert unknown.status_code == 422

        bound = client.patch(
            f"/v1/extensions/{installed['id']}/credentials",
            headers=HEADERS,
            json={"credential_refs": {"api-credential": "secret://workspace/lifecycle"}},
        )
        assert bound.status_code == 200
        assert bound.json()["health"] == "unchecked"
        assert bound.json()["status"] == "disabled"

        unresolved = client.post(
            f"/v1/extensions/{installed['id']}/health",
            headers=HEADERS,
        ).json()
        assert unresolved["status"] == "unhealthy"
        assert unresolved["details"]["unresolved_credentials"] == ["api-credential"]

        monkeypatch.setenv("ALCUIN_SECRET_WORKSPACE_LIFECYCLE", "lifecycle-token")
        healthy = client.post(
            f"/v1/extensions/{installed['id']}/health",
            headers=HEADERS,
        ).json()
        assert healthy["status"] == "healthy"
        assert healthy["details"]["reachable"] is True

        enabled = client.patch(
            f"/v1/extensions/{installed['id']}",
            headers=HEADERS,
            json={"enabled": True},
        ).json()
        assert enabled["status"] == "enabled"

        called = client.post(
            f"/v1/extensions/{installed['id']}/openapi/getRecord:call",
            headers=HEADERS,
            json={"arguments": {"id": "R-42", "verbose": "true"}},
        )
        assert called.status_code == 200
        assert called.json()["result"] == {"id": "R-42", "status": "verified"}
        assert "lifecycle-token" not in called.text
