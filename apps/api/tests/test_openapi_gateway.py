from __future__ import annotations

import httpx
import pytest

from alcuin_api.contracts import OpenAPIEntrypoint
from alcuin_api.openapi_gateway import OpenAPIGateway


@pytest.mark.asyncio
async def test_calls_read_only_openapi_tool_with_secret_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://ops.example.test/v1/incidents/INC-104?verbose=true"
        assert request.headers["authorization"] == "Bearer token-from-vault"
        return httpx.Response(200, json={"id": "INC-104", "status": "monitoring"})

    monkeypatch.setenv("ALCUIN_SECRET_WORKSPACE_OPS", "token-from-vault")
    result = await OpenAPIGateway(httpx.MockTransport(handler)).call(
        OpenAPIEntrypoint(base_url="https://ops.example.test/v1", auth="bearer"),
        {"method": "GET", "path": "/incidents/{id}", "mutating": False},
        {"id": "INC-104", "verbose": "true"},
        "secret://workspace/ops",
    )

    assert result == {
        "status_code": 200,
        "result": {"id": "INC-104", "status": "monitoring"},
    }


@pytest.mark.asyncio
async def test_rejects_mutating_openapi_tool_before_network_call() -> None:
    with pytest.raises(ValueError, match="approval-gated Agent run"):
        await OpenAPIGateway().call(
            OpenAPIEntrypoint(base_url="https://ops.example.test", auth="none"),
            {"method": "PATCH", "path": "/incidents/{id}", "mutating": True},
            {"id": "INC-104", "status": "closed"},
            None,
        )
