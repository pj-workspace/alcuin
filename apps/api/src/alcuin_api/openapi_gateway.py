from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import quote

import httpx

from alcuin_core.contracts import HealthReport, OpenAPIEntrypoint


MAX_OPENAPI_RESPONSE_BYTES = 2 * 1024 * 1024


def resolve_secret_reference(reference: str | None) -> str | None:
    if not reference:
        return None
    if not reference.startswith("secret://"):
        raise ValueError("Only secret:// credential references are accepted")
    key = re.sub(r"[^A-Za-z0-9]+", "_", reference.removeprefix("secret://")).strip("_").upper()
    return os.environ.get(f"ALCUIN_SECRET_{key}")


class OpenAPIGateway:
    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _auth_headers(
        entrypoint: OpenAPIEntrypoint,
        credential_reference: str | None,
    ) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        credential = resolve_secret_reference(credential_reference)
        if entrypoint.auth != "none" and not credential:
            raise ValueError("Credential reference could not be resolved")
        if entrypoint.auth == "bearer":
            headers["Authorization"] = f"Bearer {credential}"
        elif entrypoint.auth == "api_key":
            headers["X-API-Key"] = credential or ""
        return headers

    async def health(
        self,
        entrypoint: OpenAPIEntrypoint,
        credential_reference: str | None,
        *,
        tool_count: int,
    ) -> HealthReport:
        if not entrypoint.base_url:
            return HealthReport(
                status="unhealthy",
                details={"message": "OpenAPI extension requires a base_url"},
            )
        try:
            headers = self._auth_headers(entrypoint, credential_reference)
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = await client.head(str(entrypoint.base_url), headers=headers)
            if response.status_code in {401, 403}:
                return HealthReport(
                    status="unhealthy",
                    details={"message": "OpenAPI credential was rejected", "status_code": response.status_code},
                )
            if response.status_code >= 500:
                return HealthReport(
                    status="unhealthy",
                    details={"message": "OpenAPI endpoint is unavailable", "status_code": response.status_code},
                )
            return HealthReport(
                status="healthy",
                details={"schema": "validated", "reachable": True, "status_code": response.status_code, "tools": tool_count},
            )
        except ValueError as exc:
            return HealthReport(status="unhealthy", details={"message": str(exc)})
        except httpx.HTTPError as exc:
            return HealthReport(
                status="unhealthy",
                details={"message": "OpenAPI endpoint health check failed", "error_type": type(exc).__name__},
            )

    async def call(
        self,
        entrypoint: OpenAPIEntrypoint,
        tool: dict[str, Any],
        arguments: dict[str, Any],
        credential_reference: str | None,
        *,
        allow_mutating: bool = False,
    ) -> dict[str, Any]:
        if not entrypoint.base_url:
            raise ValueError("OpenAPI extension requires a base_url")
        if tool.get("mutating") and not allow_mutating:
            raise ValueError("Mutating OpenAPI tools require an approval-gated Agent run")
        method = str(tool.get("method", "GET")).upper()
        path = str(tool.get("path", "/"))
        remaining = dict(arguments)
        parameter_locations = tool.get("parameter_locations") or {}
        for name in re.findall(r"\{([^}]+)\}", path):
            if name not in remaining:
                raise ValueError(f"Missing path parameter: {name}")
            path = path.replace(f"{{{name}}}", quote(str(remaining.pop(name)), safe=""))
        headers = self._auth_headers(entrypoint, credential_reference)
        url = f"{str(entrypoint.base_url).rstrip('/')}/{path.lstrip('/')}"
        request_kwargs: dict[str, Any] = {"headers": headers}
        query_parameters = {
            name: remaining.pop(name)
            for name, location in parameter_locations.items()
            if location == "query" and name in remaining
        }
        if method in {"GET", "HEAD"}:
            request_kwargs["params"] = {**remaining, **query_parameters}
        else:
            if query_parameters:
                request_kwargs["params"] = query_parameters
            request_kwargs["json"] = remaining
        async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self.transport) as client:
            async with client.stream(method, url, **request_kwargs) as response:
                response.raise_for_status()
                declared_size = response.headers.get("content-length")
                if declared_size and int(declared_size) > MAX_OPENAPI_RESPONSE_BYTES:
                    raise ValueError("OpenAPI response exceeds the 2 MiB limit")
                content = await response.aread()
                if len(content) > MAX_OPENAPI_RESPONSE_BYTES:
                    raise ValueError("OpenAPI response exceeds the 2 MiB limit")
        content_type = response.headers.get("content-type", "")
        result = response.json() if "json" in content_type else content.decode("utf-8", errors="replace")
        return {"status_code": response.status_code, "result": result}
