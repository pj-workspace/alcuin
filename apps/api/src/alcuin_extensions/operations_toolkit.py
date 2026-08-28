from __future__ import annotations

from typing import Any

from alcuin_api.contracts import AgentDefinition, ExtensionManifest
from alcuin_api.store import Store
from alcuin_api.tools import ToolCitation, ToolContext, ToolError, ToolResult


def operations_demo_definition() -> AgentDefinition:
    """Return the explicit example Agent; Alcuin Core never installs it implicitly."""
    return AgentDefinition.model_validate(
        {
            "identity": {
                "name": "Operations Copilot",
                "description": "A governed operator for incidents and status communication.",
                "icon": "command",
            },
            "instructions": (
                "Help operators investigate incidents and customer records. Use read-only tools freely. "
                "Request explicit approval before changing external systems. Produce concise operational artifacts."
            ),
            "model": {
                "provider": "deepseek",
                "model": "deepseek-v4-flash-vision-exp",
                "credential_ref": "secret://workspace/deepseek-primary",
            },
            "extensions": ["ops-toolkit"],
            "tools": ["web.search", "ops.search_incidents", "ops.update_ticket"],
            "runtime": {"adapter": "langgraph-react", "max_steps": 8},
            "policies": {
                "mutating_tools": "ask",
                "external_side_effects": "ask",
            },
            "context_policy": {
                "accepted": ["page", "record", "selection"],
                "max_bytes": 16_384,
            },
            "output_schema": {"type": "artifact", "format": "markdown"},
            "starter_prompts": [
                "Summarize the active checkout incident",
                "Look up order AC-2048 and draft a customer update",
                "Update incident INC-104 to monitoring",
            ],
        }
    )


def operations_demo_manifest() -> ExtensionManifest:
    """Return the example manifest used by tests and the Operations demo host."""
    return ExtensionManifest.model_validate(
        {
            "id": "ops-toolkit",
            "name": "Operations Toolkit",
            "version": "0.1.0",
            "description": "Read operational records and perform approval-gated updates.",
            "compatibility": ">=0.1.0",
            "contributions": {
                "tools": [
                    {
                        "name": "ops.search_incidents",
                        "description": "Search incident records",
                        "input_schema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                        },
                        "mutating": False,
                    },
                    {
                        "name": "ops.update_ticket",
                        "description": "Update an incident ticket",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "ticket_id": {"type": "string"},
                                "status": {"type": "string"},
                            },
                            "required": ["ticket_id", "status"],
                            "additionalProperties": False,
                        },
                        "mutating": True,
                    },
                ],
                "skills": [{"id": "incident-brief", "name": "Incident brief"}],
                "ui_blocks": [
                    {
                        "id": "active-record",
                        "type": "card",
                        "title": "Active record",
                        "description": "Host context exposed to this Agent session.",
                        "source": {"kind": "context", "path": "record"},
                        "fields": [
                            {"label": "Record", "path": "id", "format": "text"},
                            {"label": "Type", "path": "type", "format": "status"},
                        ],
                    },
                    {
                        "id": "incident-summary",
                        "type": "table",
                        "title": "Incident results",
                        "description": "Latest structured result from Operations Toolkit.",
                        "source": {
                            "kind": "tool_result",
                            "tool": "ops.search_incidents",
                            "path": "incidents",
                        },
                        "columns": [
                            {"label": "Incident", "path": "id", "format": "text"},
                            {"label": "Status", "path": "status", "format": "status"},
                        ],
                        "empty_state": "Run an incident search to populate this table.",
                    },
                    {
                        "id": "update-incident",
                        "type": "form",
                        "title": "Update incident",
                        "description": (
                            "Creates an approval-gated Tool Run; submitting never "
                            "bypasses Agent policy."
                        ),
                        "fields": [
                            {
                                "name": "ticket_id",
                                "label": "Incident ID",
                                "input": "text",
                                "required": True,
                                "default_path": "record.id",
                            },
                            {
                                "name": "status",
                                "label": "New status",
                                "input": "select",
                                "required": True,
                                "options": ["monitoring", "resolved", "open"],
                            },
                        ],
                        "submit": {
                            "tool": "ops.update_ticket",
                            "label": "Request update",
                        },
                    },
                ],
            },
            "entrypoints": [{"type": "builtin", "adapter": "operations-demo"}],
            "permissions": [
                {
                    "id": "records:read",
                    "reason": "Read operational records",
                    "risk": "low",
                },
                {
                    "id": "tickets:write",
                    "reason": "Update incident status",
                    "risk": "high",
                },
            ],
        }
    )


def seed_operations_demo(store: Store) -> None:
    """Install Operations Copilot only when an example or test explicitly asks for it."""
    definition = operations_demo_definition()
    manifest = operations_demo_manifest()
    created_at = "2026-08-28T00:00:00+00:00"
    with store.lock, store.connection:
        store.connection.execute(
            "INSERT OR IGNORE INTO workspaces(id, name, created_at) VALUES (?, ?, ?)",
            ("ws_demo", "Alcuin Workspace", created_at),
        )
        store.connection.execute(
            """INSERT OR IGNORE INTO agents
            (id, workspace_id, slug, name, description, status, current_version_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "agt_operations",
                "ws_demo",
                "operations-copilot",
                definition.identity.name,
                definition.identity.description,
                "published",
                "av_operations_1",
                created_at,
            ),
        )
        store.connection.execute(
            """INSERT OR IGNORE INTO agent_versions
            (id, workspace_id, agent_id, version, definition_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "av_operations_1",
                "ws_demo",
                "agt_operations",
                1,
                definition.model_dump_json(),
                created_at,
            ),
        )
        store.connection.execute(
            """INSERT OR IGNORE INTO extensions
            (id, workspace_id, manifest_id, name, version, status, health, manifest_json,
             credential_refs_json, installed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "ext_ops_toolkit",
                "ws_demo",
                manifest.id,
                manifest.name,
                manifest.version,
                "enabled",
                "healthy",
                manifest.model_dump_json(),
                "{}",
                created_at,
            ),
        )


async def operations_demo_adapter(
    context: ToolContext,
    tool_name: str,
    arguments: dict[str, Any],
) -> ToolResult:
    """Trusted in-process adapter for the explicit Operations Toolkit demo."""
    if tool_name == "ops.search_incidents":
        return ToolResult(
            data={
                "query": str(arguments.get("query", "")),
                "incidents": [
                    {
                        "id": context.thread_context.get("record", {}).get(
                            "id", "INC-104"
                        ),
                        "status": "monitoring",
                    }
                ],
            },
            summary="One matching incident found",
            citations=(
                ToolCitation(
                    label="Active incident",
                    source="Operations Toolkit",
                    locator=(
                        "ops://incidents/"
                        + str(
                            context.thread_context.get("record", {}).get(
                                "id", "INC-104"
                            )
                        )
                    ),
                ),
            ),
        )
    if tool_name == "ops.update_ticket":
        return ToolResult(
            data={
                "ticket_id": str(arguments["ticket_id"]),
                "status": str(arguments["status"]),
                "updated": True,
            },
            summary="Incident ticket updated",
        )
    raise ToolError("tool_not_allowed", "Operations Toolkit does not expose this tool")
