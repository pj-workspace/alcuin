from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from jsonschema import validate
from pydantic import ValidationError

from alcuin_api.contracts import (
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
        (REPOSITORY_ROOT / "extensions/operations-toolkit/alcuin.extension.json").read_text()
    )
    validate(instance=manifest, schema=schema)


def test_stdio_entrypoint_requires_command() -> None:
    with pytest.raises(ValidationError):
        MCPEntrypoint(transport="stdio")


def test_credentials_are_references_not_values() -> None:
    manifest = ExtensionManifest(id="sample.tools", name="Sample Tools", version="0.1.0")
    with pytest.raises(ValidationError):
        ExtensionInstallRequest(manifest=manifest, credential_refs={"api": "plain-secret"})


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
    resolved = await resolve_openapi_document(request, httpx.MockTransport(handler))
    manifest = manifest_from_openapi(resolved)

    assert manifest.contributions.tools[0]["name"] == "listOrders"
    assert str(manifest.entrypoints[0].spec_url) == "https://schemas.example.test/orders.yaml"
