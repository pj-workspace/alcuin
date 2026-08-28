"""Install the explicit Operations demo records into a compatible Store."""

from typing import Any

from .extension import operations_demo_definition, operations_demo_manifest


def seed_operations_demo(store: Any) -> None:
    """Seed demo data without importing the API application package."""
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
