from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import quote

import httpx

from .contracts import OpenAPIEntrypoint


def resolve_secret_reference(reference: str | None) -> str | None:
    if not reference:
        return None
    if not reference.startswith("secret://"):
        raise ValueError("Only secret:// credential references are accepted")
    key = re.sub(r"[^A-Za-z0-9]+", "_", reference.removeprefix("secret://")).strip("_").upper()
    return os.environ.get(f"ALCUIN_SECRET_{key}")


class OpenAPIGateway:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def call(
        self,
        entrypoint: OpenAPIEntrypoint,
        tool: dict[str, Any],
        arguments: dict[str, Any],
        credential_reference: str | None,
    ) -> dict[str, Any]:
        if not entrypoint.base_url:
            raise ValueError("OpenAPI extension requires a base_url")
        if tool.get("mutating"):
            raise ValueError("Mutating OpenAPI tools require an approval-gated Agent run")
        method = str(tool.get("method", "GET")).upper()
        path = str(tool.get("path", "/"))
        remaining = dict(arguments)
        for name in re.findall(r"\{([^}]+)\}", path):
            if name not in remaining:
                raise ValueError(f"Missing path parameter: {name}")
            path = path.replace(f"{{{name}}}", quote(str(remaining.pop(name)), safe=""))
        headers: dict[str, str] = {"Accept": "application/json"}
        credential = resolve_secret_reference(credential_reference)
        if entrypoint.auth != "none" and not credential:
            raise ValueError("Credential reference could not be resolved")
        if entrypoint.auth == "bearer":
            headers["Authorization"] = f"Bearer {credential}"
        elif entrypoint.auth == "api_key":
            headers["X-API-Key"] = credential or ""
        url = f"{str(entrypoint.base_url).rstrip('/')}/{path.lstrip('/')}"
        request_kwargs: dict[str, Any] = {"headers": headers}
        if method in {"GET", "HEAD"}:
            request_kwargs["params"] = remaining
        else:
            request_kwargs["json"] = remaining
        async with httpx.AsyncClient(timeout=30, transport=self.transport) as client:
            response = await client.request(method, url, **request_kwargs)
            response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        result = response.json() if "json" in content_type else response.text
        return {"status_code": response.status_code, "result": result}
