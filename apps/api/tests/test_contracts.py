from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from jsonschema import validate
from pydantic import ValidationError

from alcuin_core.contracts import (
    ExtensionInstallRequest,
    ExtensionManifest,
    MCPEntrypoint,
    OpenAPIImportRequest,
)
from alcuin_api.extensions import manifest_from_openapi, resolve_openapi_document


REPOSITORY_ROOT = Path(__file__).parents[3]


def test_sample_manifest_matches_public_json_schema() -> None:
    schema = json.loads(
        (REPOSITORY_ROOT / "docs/schemas/alcuin-extension.schema.json").read_text()
    )
    manifest = json.loads(
        (REPOSITORY_ROOT / "extensions/operations-copilot/alcuin.extension.json").read_text()
    )
    validate(instance=manifest, schema=schema)
    parsed = ExtensionManifest.model_validate(manifest)
    assert parsed.schema_uri == "../../docs/schemas/alcuin-extension.schema.json"
    assert "schema_uri" not in parsed.model_dump(mode="json")
    assert [block.type for block in parsed.contributions.ui_blocks] == [
        "card",
        "table",
        "form",
    ]


def test_stdio_entrypoint_requires_command() -> None:
    with pytest.raises(ValidationError):
        MCPEntrypoint(transport="stdio")


def test_credentials_are_references_not_values() -> None:
    manifest = ExtensionManifest(
        id="sample.tools",
        name="Sample Tools",
        version="0.1.0",
        entrypoints=[{"type": "builtin", "adapter": "sample"}],
    )
    with pytest.raises(ValidationError):
        ExtensionInstallRequest(manifest=manifest, credential_refs={"api": "plain-secret"})


def test_native_manifest_rejects_unsafe_mutation_contract() -> None:
    base = {
        "id": "unsafe.writer",
        "name": "Unsafe Writer",
        "version": "0.1.0",
        "contributions": {
            "tools": [
                {
                    "name": "write",
                    "input_schema": {"type": "object"},
                    "mutating": True,
                    "approval": "auto",
                }
            ]
        },
        "entrypoints": [{"type": "builtin", "adapter": "unsafe-writer"}],
        "permissions": [
            {
                "id": "records:write",
                "reason": "Write records",
                "risk": "high",
            }
        ],
    }
    with pytest.raises(ValidationError, match="must require approval"):
        ExtensionManifest.model_validate(base)

    base["contributions"]["tools"][0]["approval"] = "ask"
    base["permissions"][0]["risk"] = "low"
    with pytest.raises(ValidationError, match="high-risk permission"):
        ExtensionManifest.model_validate(base)


def test_ui_blocks_can_only_bind_declared_tools() -> None:
    manifest = {
        "id": "sample.ui",
        "name": "Sample UI",
        "version": "0.1.0",
        "contributions": {
            "tools": [
                {
                    "name": "search",
                    "input_schema": {"type": "object"},
                    "mutating": False,
                }
            ],
            "ui_blocks": [
                {
                    "id": "results",
                    "type": "table",
                    "title": "Results",
                    "source": {
                        "kind": "tool_result",
                        "tool": "undeclared",
                        "path": "rows",
                    },
                    "columns": [{"label": "ID", "path": "id"}],
                }
            ],
        },
        "entrypoints": [{"type": "builtin", "adapter": "sample"}],
    }
    with pytest.raises(ValidationError, match="undeclared tool"):
        ExtensionManifest.model_validate(manifest)

    manifest["contributions"]["ui_blocks"] = [
        {
            "id": "action",
            "type": "form",
            "title": "Action",
            "fields": [{"name": "query", "label": "Query", "input": "text"}],
            "submit": {"tool": "undeclared", "label": "Run"},
        }
    ]
    with pytest.raises(ValidationError, match="undeclared tool"):
        ExtensionManifest.model_validate(manifest)

def test_openapi_import_marks_mutations_for_approval() -> None:
    request = OpenAPIImportRequest(
        name="Orders API",
        extension_id="orders.api",
        auth="bearer",
        spec={
            "openapi": "3.1.0",
            "info": {"title": "Orders", "version": "1.2.0"},
            "servers": [{"url": "https://orders.example.test/v1"}],
            "paths": {
                "/orders/{id}": {
                    "get": {"operationId": "getOrder", "summary": "Get order"},
                    "patch": {"operationId": "updateOrder", "summary": "Update order"},
                }
            },
        },
    )
    manifest = manifest_from_openapi(request)
    tools = {tool["name"]: tool for tool in manifest.contributions.tools}
    assert tools["getOrder"]["approval"] == "auto"
    assert tools["updateOrder"]["approval"] == "ask"
    assert {permission.id for permission in manifest.permissions} == {"api:read", "api:write"}
    assert str(manifest.entrypoints[0].base_url).rstrip("/") == "https://orders.example.test/v1"


def test_openapi_import_resolves_request_body_component_reference() -> None:
    request = OpenAPIImportRequest(
        name="Records API",
        extension_id="records.api",
        spec={
            "openapi": "3.1.0",
            "info": {"title": "Records", "version": "1.0.0"},
            "servers": [{"url": "https://records.example.test"}],
            "components": {
                "schemas": {
                    "RecordPatch": {
                        "type": "object",
                        "properties": {
                            "status": {
                                "type": "string",
                                "enum": ["open", "closed"],
                            }
                        },
                        "required": ["status"],
                    }
                }
            },
            "paths": {
                "/records/{record_id}": {
                    "patch": {
                        "operationId": "updateRecord",
                        "parameters": [
                            {
                                "name": "record_id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/RecordPatch"
                                    }
                                }
                            },
                        },
                    }
                }
            },
        },
    )

    tool = manifest_from_openapi(request).contributions.tools[0]

    assert tool["input_schema"]["required"] == ["record_id", "status"]
    assert tool["input_schema"]["properties"]["status"]["enum"] == [
        "open",
        "closed",
    ]
    assert tool["parameter_locations"]["status"] == "body"


@pytest.mark.asyncio
async def test_openapi_import_resolves_remote_yaml_document() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://schemas.example.test/orders.yaml"
        return httpx.Response(
            200,
            text="""openapi: 3.1.0
info:
  title: Orders
  version: 1.2.0
servers:
  - url: https://orders.example.test/v1
paths:
  /orders:
    get:
      operationId: listOrders
""",
            headers={"content-type": "application/yaml"},
        )

    request = OpenAPIImportRequest(
        name="Orders API",
        extension_id="orders.api",
        spec_url="https://schemas.example.test/orders.yaml",
    )
    async def allow_test_url(_url: str) -> bool:
        return True

    resolved = await resolve_openapi_document(
        request,
        httpx.MockTransport(handler),
        url_validator=allow_test_url,
    )
    manifest = manifest_from_openapi(resolved)

    assert manifest.contributions.tools[0]["name"] == "listOrders"
    assert str(manifest.entrypoints[0].spec_url) == "https://schemas.example.test/orders.yaml"
