from __future__ import annotations

import time

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from alcuin_api.config import Settings
from alcuin_api.security import (
    RequestScope,
    issue_embed_token,
    redact_sensitive,
    redact_text,
    resolve_scope,
)
from alcuin_core.contracts import EmbedClaims


def _scope_client(settings: Settings) -> TestClient:
    app = FastAPI()
    app.state.settings = settings

    @app.get("/scope")
    async def scope(current: RequestScope = Depends(resolve_scope)) -> dict[str, object]:
        return {
            "workspace_id": current.workspace_id,
            "embed": current.embed,
            "agent_id": current.agent_id,
        }

    return TestClient(app)


def test_redacts_secret_shaped_tool_arguments_and_error_values() -> None:
    payload = {
        "tool": "ops.lookup",
        "arguments": {
            "query": "incident",
            "apiKey": "raw-key",
            "nested": {"authorization": "Bearer raw-token"},
        },
    }

    assert redact_sensitive(payload) == {
        "tool": "ops.lookup",
        "arguments": {
            "query": "incident",
            "apiKey": "[REDACTED]",
            "nested": {"authorization": "[REDACTED]"},
        },
    }
    assert redact_text("provider rejected raw-key", ["raw-key"]) == "provider rejected [REDACTED]"


@pytest.mark.parametrize("environment", ["development", "test"])
def test_local_operator_scope_accepts_workspace_header(environment: str) -> None:
    with _scope_client(Settings(environment=environment)) as client:
        response = client.get(
            "/scope", headers={"X-Alcuin-Workspace": "ws_local"}
        )

    assert response.status_code == 200
    assert response.json() == {
        "workspace_id": "ws_local",
        "embed": False,
        "agent_id": None,
    }


def test_production_requires_configured_operator_api_key() -> None:
    with pytest.raises(ValidationError, match="ALCUIN_OPERATOR_API_KEY"):
        Settings(environment="production", operator_api_key=None)


def test_production_operator_requests_fail_closed_without_matching_bearer() -> None:
    secret = "production-operator-secret"
    settings = Settings(environment="production", operator_api_key=secret)
    assert secret not in repr(settings)

    with _scope_client(settings) as client:
        header_only = client.get(
            "/scope", headers={"X-Alcuin-Workspace": "ws_demo"}
        )
        wrong_key = client.get(
            "/scope",
            headers={
                "Authorization": "Bearer wrong-operator-secret",
                "X-Alcuin-Workspace": "ws_demo",
            },
        )
        missing_workspace = client.get(
            "/scope", headers={"Authorization": f"Bearer {secret}"}
        )
        allowed = client.get(
            "/scope",
            headers={
                "Authorization": f"Bearer {secret}",
                "X-Alcuin-Workspace": "ws_demo",
            },
        )

    assert header_only.status_code == 401
    assert wrong_key.status_code == 401
    assert header_only.json() == wrong_key.json() == {
        "detail": "Operator authentication required"
    }
    assert header_only.headers["www-authenticate"] == "Bearer"
    assert secret not in header_only.text
    assert secret not in wrong_key.text
    assert missing_workspace.status_code == 401
    assert missing_workspace.json() == {"detail": "Workspace header required"}
    assert allowed.status_code == 200
    assert allowed.json() == {
        "workspace_id": "ws_demo",
        "embed": False,
        "agent_id": None,
    }


def test_production_operator_key_uses_constant_time_comparison(monkeypatch) -> None:
    import alcuin_api.security as security

    calls: list[tuple[bytes, bytes]] = []
    compare_digest = security.hmac.compare_digest

    def recording_compare(left: bytes, right: bytes) -> bool:
        calls.append((left, right))
        return compare_digest(left, right)

    monkeypatch.setattr(security.hmac, "compare_digest", recording_compare)
    settings = Settings(
        environment="production", operator_api_key="production-operator-secret"
    )
    with _scope_client(settings) as client:
        response = client.get(
            "/scope",
            headers={
                "Authorization": "Bearer wrong-operator-secret",
                "X-Alcuin-Workspace": "ws_demo",
            },
        )

    assert response.status_code == 401
    assert calls == [(b"wrong-operator-secret", b"production-operator-secret")]


def test_production_keeps_alc1_embed_tokens_independent_of_operator_key() -> None:
    now = int(time.time())
    token = issue_embed_token(
        EmbedClaims(
            workspace_id="ws_embed",
            agent_id="agt_embed",
            agent_version_id="agv_embed",
            origin="https://host.example",
            allowed_actions=["run:read"],
            issued_at=now,
            expires_at=now + 60,
        )
    )
    settings = Settings(
        environment="production", operator_api_key="production-operator-secret"
    )

    with _scope_client(settings) as client:
        response = client.get(
            "/scope",
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": "https://host.example",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "workspace_id": "ws_embed",
        "embed": True,
        "agent_id": "agt_embed",
    }
