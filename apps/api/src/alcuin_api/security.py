from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Iterable

from fastapi import Header, HTTPException, Request, status

from .config import get_settings
from .contracts import EmbedClaims


SENSITIVE_KEY_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
)


def redact_sensitive(value: Any) -> Any:
    """Remove secret-shaped fields before tool data reaches persisted events or approvals."""
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if any(marker in key.lower() for marker in SENSITIVE_KEY_MARKERS)
                else redact_sensitive(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def redact_text(value: str, secrets: Iterable[str | None]) -> str:
    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_embed_token(claims: EmbedClaims) -> str:
    payload = _b64encode(claims.model_dump_json().encode())
    secret = get_settings().signing_secret.encode()
    signature = _b64encode(hmac.new(secret, payload.encode(), hashlib.sha256).digest())
    return f"alc1.{payload}.{signature}"


def verify_embed_token(token: str, origin: str | None) -> EmbedClaims:
    try:
        prefix, payload, signature = token.split(".")
        if prefix != "alc1":
            raise ValueError("unsupported token")
        expected = hmac.new(
            get_settings().signing_secret.encode(), payload.encode(), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _b64decode(signature)):
            raise ValueError("invalid signature")
        claims = EmbedClaims.model_validate(json.loads(_b64decode(payload)))
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid embed token") from exc
    if claims.expires_at <= int(time.time()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Embed token expired")
    if origin and claims.origin.rstrip("/") != origin.rstrip("/"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Embed origin mismatch")
    return claims


@dataclass(frozen=True)
class RequestScope:
    workspace_id: str
    agent_id: str | None = None
    agent_version_id: str | None = None
    allowed_actions: frozenset[str] | None = None
    embed: bool = False

    def require(self, action: str) -> None:
        if self.allowed_actions is not None and action not in self.allowed_actions:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Action not allowed: {action}")


async def resolve_scope(
    request: Request,
    x_alcuin_workspace: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> RequestScope:
    if authorization and authorization.startswith("Bearer "):
        claims = verify_embed_token(authorization.removeprefix("Bearer ").strip(), request.headers.get("origin"))
        return RequestScope(
            workspace_id=claims.workspace_id,
            agent_id=claims.agent_id,
            agent_version_id=claims.agent_version_id,
            allowed_actions=frozenset(claims.allowed_actions),
            embed=True,
        )
    if not x_alcuin_workspace:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Workspace header required")
    return RequestScope(workspace_id=x_alcuin_workspace)
