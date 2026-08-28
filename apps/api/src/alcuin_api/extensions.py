from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import yaml

from alcuin_core.contracts import (
    ExtensionContributions,
    ExtensionManifest,
    HealthReport,
    MCPImportRequest,
    OpenAPIEntrypoint,
    OpenAPIImportRequest,
    PermissionSpec,
)
from .mcp_gateway import MCPGateway
from .openapi_gateway import OpenAPIGateway, resolve_secret_reference
from alcuin_web_search import is_public_http_url


MUTATING_METHODS = {"post", "put", "patch", "delete"}
MAX_OPENAPI_DOCUMENT_BYTES = 2 * 1024 * 1024
UrlValidator = Callable[[str], Awaitable[bool]]


def _resolve_local_reference(document: dict[str, Any], value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    reference = value.get("$ref")
    if not isinstance(reference, str) or not reference.startswith("#/"):
        return value
    target: Any = document
    for segment in reference[2:].split("/"):
        if not isinstance(target, dict):
            return value
        target = target.get(segment.replace("~1", "/").replace("~0", "~"))
    if not isinstance(target, dict):
        return value
    return {**target, **{key: item for key, item in value.items() if key != "$ref"}}


def _parse_openapi_text(content: str) -> dict[str, Any]:
    if len(content.encode("utf-8")) > MAX_OPENAPI_DOCUMENT_BYTES:
        raise ValueError("OpenAPI document exceeds the 2 MiB import limit")
    try:
        document = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ValueError("OpenAPI document is not valid JSON or YAML") from exc
    if not isinstance(document, dict):
        raise ValueError("OpenAPI document must contain an object at its root")
    return document


async def resolve_openapi_document(
    payload: OpenAPIImportRequest,
    transport: httpx.AsyncBaseTransport | None = None,
    *,
    allow_private_networks: bool = False,
    url_validator: UrlValidator = is_public_http_url,
) -> OpenAPIImportRequest:
    if payload.spec:
        return payload
    if payload.spec_text:
        return payload.model_copy(update={"spec": _parse_openapi_text(payload.spec_text)})
    spec_url = str(payload.spec_url)
    if not allow_private_networks and not await url_validator(spec_url):
        raise ValueError("OpenAPI spec_url must resolve to a public HTTP endpoint")
    async with httpx.AsyncClient(timeout=20, follow_redirects=False, transport=transport) as client:
        response = await client.get(str(payload.spec_url), headers={"Accept": "application/json, application/yaml, text/yaml"})
        response.raise_for_status()
    return payload.model_copy(update={"spec": _parse_openapi_text(response.text)})


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
            parameters = [
                item
                for item in [
                    *(path_item.get("parameters") or []),
                    *(operation.get("parameters") or []),
                ]
                if isinstance(item, dict)
                and item.get("in") in {"path", "query"}
                and isinstance(item.get("name"), str)
            ]
            properties: dict[str, Any] = {}
            required: list[str] = []
            parameter_locations: dict[str, str] = {}
            for parameter in parameters:
                name = str(parameter["name"])
                parameter_schema = _resolve_local_reference(
                    payload.spec, parameter.get("schema")
                )
                properties[name] = (
                    parameter_schema
                    if isinstance(parameter_schema, dict)
                    else {"type": "string"}
                )
                parameter_locations[name] = str(parameter["in"])
                if parameter.get("required") or parameter.get("in") == "path":
                    required.append(name)
            request_body = operation.get("requestBody")
            if isinstance(request_body, dict):
                body_content = request_body.get("content") or {}
                json_body = body_content.get("application/json") if isinstance(body_content, dict) else None
                body_schema = _resolve_local_reference(
                    payload.spec,
                    json_body.get("schema") if isinstance(json_body, dict) else None,
                )
                if isinstance(body_schema, dict) and body_schema.get("type") == "object":
                    for name, schema in (body_schema.get("properties") or {}).items():
                        if isinstance(name, str) and isinstance(schema, dict):
                            properties[name] = schema
                            parameter_locations[name] = "body"
                    required.extend(
                        str(name)
                        for name in body_schema.get("required", [])
                        if isinstance(name, str)
                    )
            input_schema: dict[str, Any] = {
                "type": "object",
                "properties": properties,
                "additionalProperties": False,
            }
            if required:
                input_schema["required"] = sorted(set(required))
            tools.append(
                {
                    "name": operation_id,
                    "description": operation.get("summary") or operation.get("description") or f"{method.upper()} {path}",
                    "method": method.upper(),
                    "path": path,
                    "input_schema": input_schema,
                    "parameter_locations": parameter_locations,
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


def manifest_from_mcp(
    payload: MCPImportRequest,
    discovered_tools: list[dict[str, Any]],
) -> ExtensionManifest:
    tools: list[dict[str, Any]] = []
    permissions: dict[str, PermissionSpec] = {}
    for discovered in discovered_tools:
        if payload.selected_tools and discovered.get("name") not in payload.selected_tools:
            continue
        annotations = discovered.get("annotations")
        read_only = bool(
            isinstance(annotations, dict)
            and (annotations.get("readOnlyHint") or annotations.get("read_only_hint"))
        )
        mutating = not read_only
        tools.append(
            {
                "name": str(discovered.get("name") or ""),
                "description": str(discovered.get("description") or "")[:500],
                "input_schema": (
                    discovered.get("input_schema")
                    if isinstance(discovered.get("input_schema"), dict)
                    else {"type": "object", "properties": {}}
                ),
                "output_schema": discovered.get("output_schema"),
                "mutating": mutating,
                "approval": "ask" if mutating else "auto",
            }
        )
        permission_id = "tools:write" if mutating else "tools:read"
        permissions[permission_id] = PermissionSpec(
            id=permission_id,
            reason="Call MCP tools that may change external systems"
            if mutating
            else "Call read-only MCP tools",
            risk="high" if mutating else "low",
        )
    return ExtensionManifest(
        id=payload.extension_id,
        name=payload.name,
        version=payload.version,
        description=payload.description or f"Imported from {payload.name} MCP server",
        contributions=ExtensionContributions(tools=tools),
        entrypoints=[payload.entrypoint],
        permissions=list(permissions.values()),
    )


def refresh_mcp_manifest(
    manifest: ExtensionManifest,
    discovered_tools: list[dict[str, Any]],
) -> ExtensionManifest:
    entrypoint = next(
        (item for item in manifest.entrypoints if item.type == "mcp"),
        None,
    )
    if entrypoint is None:
        raise ValueError("Extension has no MCP entrypoint")
    approved_names = {
        str(tool.get("name"))
        for tool in manifest.contributions.tools
        if tool.get("name")
    }
    approved_tools = [
        tool for tool in discovered_tools if tool.get("name") in approved_names
    ]
    refreshed = manifest_from_mcp(
        MCPImportRequest(
            name=manifest.name,
            extension_id=manifest.id,
            version=manifest.version,
            description=manifest.description,
            entrypoint=entrypoint,
        ),
        approved_tools,
    )
    contributions = manifest.contributions.model_copy(
        update={"tools": refreshed.contributions.tools}
    )
    unrelated_permissions = [
        permission
        for permission in manifest.permissions
        if permission.id not in {"tools:read", "tools:write"}
    ]
    return manifest.model_copy(
        update={
            "contributions": contributions,
            "permissions": [*unrelated_permissions, *refreshed.permissions],
        }
    )


def required_credential_ids(manifest: ExtensionManifest) -> set[str]:
    return {
        str(requirement.get("id"))
        for requirement in manifest.credential_requirements
        if requirement.get("required", True) and requirement.get("id")
    }


def missing_credential_ids(extension: dict[str, Any]) -> list[str]:
    manifest = ExtensionManifest.model_validate(extension["manifest"])
    refs = extension.get("credential_refs") or {}
    return sorted(required_credential_ids(manifest) - set(refs))


async def check_extension_health(
    extension: dict[str, Any],
    *,
    mcp_gateway: MCPGateway | None = None,
    openapi_gateway: OpenAPIGateway | None = None,
) -> HealthReport:
    manifest = ExtensionManifest.model_validate(extension["manifest"])
    missing_credentials = missing_credential_ids(extension)
    if missing_credentials:
        return HealthReport(
            status="unhealthy",
            details={"message": "Required credential references are not bound", "missing_credentials": missing_credentials},
        )
    unresolved_credentials = [
        identifier
        for identifier, reference in (extension.get("credential_refs") or {}).items()
        if resolve_secret_reference(reference) is None
    ]
    if unresolved_credentials:
        return HealthReport(
            status="unhealthy",
            details={"message": "Credential references could not be resolved", "unresolved_credentials": sorted(unresolved_credentials)},
        )
    if not manifest.entrypoints:
        return HealthReport(status="degraded", details={"message": "No runtime entrypoint declared"})
    entrypoint = manifest.entrypoints[0]
    if entrypoint.type == "builtin":
        return HealthReport(
            status="healthy",
            details={"adapter": entrypoint.adapter, "tools": len(manifest.contributions.tools)},
        )
    if entrypoint.type == "openapi":
        credential_reference = extension.get("credential_refs", {}).get("api-credential")
        return await (openapi_gateway or OpenAPIGateway()).health(
            entrypoint,
            credential_reference,
            tool_count=len(manifest.contributions.tools),
        )
    try:
        tools = await (mcp_gateway or MCPGateway()).discover(entrypoint)
        return HealthReport(
            status="healthy",
            details={"transport": entrypoint.transport, "tools": tools},
        )
    except Exception as exc:
        return HealthReport(
            status="unhealthy",
            details={
                "transport": entrypoint.transport,
                "message": "MCP connection or discovery failed",
                "error_type": type(exc).__name__,
                "tools": [],
            },
        )
