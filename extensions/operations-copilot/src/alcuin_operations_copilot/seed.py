"""Install the explicit Operations demo records into a compatible Store."""

import hashlib
from typing import Any

from .extension import operations_demo_definition, operations_demo_manifest


def seed_operations_demo(store: Any) -> None:
    """Seed demo data without importing the API application package."""
    definition = operations_demo_definition()
    manifest = operations_demo_manifest()
    created_at = "2026-08-28T00:00:00+00:00"
    definition_json = definition.model_dump_json()
    definition_sha256 = hashlib.sha256(definition_json.encode("utf-8")).hexdigest()
    with store.lock, store.connection:
        store.connection.execute(
            """INSERT INTO workspaces(id, name, created_at) VALUES (?, ?, ?)
            ON CONFLICT DO NOTHING""",
            ("ws_demo", "Alcuin Workspace", created_at),
        )
        store.connection.execute(
            """INSERT INTO agents
            (id, workspace_id, slug, name, description, status, current_version_id,
             published_version_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING""",
            (
                "agt_operations",
                "ws_demo",
                "operations-copilot",
                definition.identity.name,
                definition.identity.description,
                "published",
                "av_operations_1",
                "av_operations_1",
                created_at,
                created_at,
            ),
        )
        store.connection.execute(
            """INSERT INTO agent_versions
            (id, workspace_id, agent_id, version, definition_json, definition_sha256,
             created_at, published_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING""",
            (
                "av_operations_1",
                "ws_demo",
                "agt_operations",
                1,
                definition_json,
                definition_sha256,
                created_at,
                created_at,
            ),
        )
        store.connection.execute(
            """INSERT INTO extensions
            (id, workspace_id, manifest_id, name, version, status, health, manifest_json,
             credential_refs_json, installed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING""",
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
