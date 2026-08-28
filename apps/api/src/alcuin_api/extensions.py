from __future__ import annotations

from typing import Any

import httpx
import yaml

from .contracts import (
    ExtensionContributions,
    ExtensionManifest,
    HealthReport,
    OpenAPIEntrypoint,
    OpenAPIImportRequest,
    PermissionSpec,
)
from .mcp_gateway import MCPGateway


MUTATING_METHODS = {"post", "put", "patch", "delete"}
MAX_OPENAPI_DOCUMENT_BYTES = 2 * 1024 * 1024


async def resolve_openapi_document(
    payload: OpenAPIImportRequest,
    transport: httpx.AsyncBaseTransport | None = None,
) -> OpenAPIImportRequest:
    if payload.spec:
        return payload
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, transport=transport) as client:
        response = await client.get(str(payload.spec_url), headers={"Accept": "application/json, application/yaml, text/yaml"})
        response.raise_for_status()
    if len(response.content) > MAX_OPENAPI_DOCUMENT_BYTES:
        raise ValueError("OpenAPI document exceeds the 2 MiB import limit")
    try:
        document = yaml.safe_load(response.text)
    except yaml.YAMLError as exc:
        raise ValueError("OpenAPI document is not valid JSON or YAML") from exc
    if not isinstance(document, dict):
        raise ValueError("OpenAPI document must contain an object at its root")
    return payload.model_copy(update={"spec": document})


def inspect_manifest(manifest: ExtensionManifest) -> dict[str, Any]:
    high_risk = [permission.id for permission in manifest.permissions if permission.risk == "high"]
    transports = [entrypoint.model_dump(mode="json") for entrypoint in manifest.entrypoints]
    return {
        "valid": True,
        "manifest": manifest.model_dump(mode="json"),
        "permission_summary": {
            "total": len(manifest.permissions),
            "high_risk": high_risk,
            "requires_review": bool(high_risk),
        },
        "entrypoints": transports,
        "install_state": "ready_for_disabled_install",
    }


def manifest_from_openapi(payload: OpenAPIImportRequest) -> ExtensionManifest:
    if payload.spec is None:
        raise ValueError("Resolve spec_url before creating an extension manifest")
    info = payload.spec.get("info", {})
    tools: list[dict[str, Any]] = []
    permissions: dict[str, PermissionSpec] = {}
    for path, path_item in payload.spec.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"} or not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId") or f"{method}_{path}".replace("/", "_").strip("_")
            if payload.selected_operations and operation_id not in payload.selected_operations:
                continue
            mutating = method.lower() in MUTATING_METHODS
            tools.append(
                {
                    "name": operation_id,
                    "description": operation.get("summary") or operation.get("description") or f"{method.upper()} {path}",
                    "method": method.upper(),
                    "path": path,
                    "input_schema": {"type": "object", "properties": {}},
                    "mutating": mutating,
                    "approval": "ask" if mutating else "auto",
                }
            )
            permission_id = "api:write" if mutating else "api:read"
            permissions[permission_id] = PermissionSpec(
                id=permission_id,
                reason="Call mutating OpenAPI operations" if mutating else "Read data through OpenAPI operations",
                risk="high" if mutating else "low",
            )
    server_url = payload.base_url
    if not server_url:
        servers = payload.spec.get("servers", [])
        if servers and isinstance(servers[0], dict):
            server_url = servers[0].get("url")
    entrypoint = OpenAPIEntrypoint(
        auth=payload.auth,
        base_url=server_url,
        spec_url=payload.spec_url,
    )
    return ExtensionManifest(
        id=payload.extension_id,
        name=payload.name,
        version=str(info.get("version", "0.1.0")) if str(info.get("version", "0.1.0")).count(".") >= 2 else "0.1.0",
        description=str(info.get("description", f"Imported from {payload.name} OpenAPI specification"))[:500],
        contributions=ExtensionContributions(tools=tools),
        entrypoints=[entrypoint],
        permissions=list(permissions.values()),
        credential_requirements=(
            [{"id": "api-credential", "type": payload.auth, "required": True}]
            if payload.auth != "none"
            else []
        ),
    )


async def check_extension_health(extension: dict[str, Any]) -> HealthReport:
    manifest = ExtensionManifest.model_validate(extension["manifest"])
    if not manifest.entrypoints:
        return HealthReport(status="degraded", details={"message": "No runtime entrypoint declared"})
    entrypoint = manifest.entrypoints[0]
    if entrypoint.type == "builtin":
        return HealthReport(
            status="healthy",
            details={"adapter": entrypoint.adapter, "tools": len(manifest.contributions.tools)},
        )
    if entrypoint.type == "openapi":
        return HealthReport(
            status="healthy",
            details={"schema": "validated", "tools": len(manifest.contributions.tools)},
        )
    try:
        tools = await MCPGateway().discover(entrypoint)
        return HealthReport(
            status="healthy",
            details={"transport": entrypoint.transport, "tools": tools},
        )
    except Exception as exc:
        return HealthReport(
            status="unhealthy",
            details={
                "transport": entrypoint.transport,
                "message": str(exc)[:300],
                "tools": [],
            },
        )
