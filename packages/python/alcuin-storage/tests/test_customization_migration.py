from __future__ import annotations

import hashlib
import os
from pathlib import Path
from urllib.parse import urlsplit

from alembic import command
from alembic.config import Config
import psycopg
import pytest


ROOT = Path(__file__).resolve().parents[4]
DATABASE_URL = os.environ.get("ALCUIN_TEST_POSTGRES_URL")
MIGRATION_URL = os.environ.get("ALCUIN_DATABASE_URL")


def require_isolated_test_database(url: str | None) -> str | None:
    if url is None:
        return None
    if not urlsplit(url).path.removeprefix("/").endswith("_test"):
        raise RuntimeError(
            "Refusing to migrate a PostgreSQL database whose name does not end in '_test'"
        )
    return url


DATABASE_URL = require_isolated_test_database(DATABASE_URL)
MIGRATION_URL = require_isolated_test_database(MIGRATION_URL)
if DATABASE_URL and MIGRATION_URL and DATABASE_URL != MIGRATION_URL:
    raise RuntimeError("Migration and contract URLs must reference the same _test database")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL or not MIGRATION_URL,
    reason="Isolated ALCUIN_TEST_POSTGRES_URL is required for migration tests",
)


def alembic_config() -> Config:
    return Config(str(ROOT / "packages/python/alcuin-storage/alembic.ini"))


def test_customization_migration_backfills_thread_configuration_and_round_trips() -> None:
    assert DATABASE_URL is not None
    config = alembic_config()
    created_at = "2026-08-29T00:00:00+00:00"
    definition_json = (
        '{"identity":{"name":"Legacy Agent","description":""},'
        '"instructions":"Legacy instructions","model":{"provider":"test",'
        '"model":"test"}}'
    )
    definition_sha256 = hashlib.sha256(definition_json.encode("utf-8")).hexdigest()
    try:
        command.downgrade(config, "20260829_0003")
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute("TRUNCATE TABLE workspaces CASCADE")
            connection.execute(
                "INSERT INTO workspaces(id, name, created_at) VALUES (%s, %s, %s)",
                ("ws_legacy", "Legacy Workspace", created_at),
            )
            connection.execute(
                """INSERT INTO agents
                (id, workspace_id, slug, name, description, status,
                 current_version_id, published_version_id, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, %s, %s)""",
                (
                    "agt_legacy",
                    "ws_legacy",
                    "legacy-agent",
                    "Legacy Agent",
                    "Legacy",
                    "draft",
                    "av_legacy_1",
                    created_at,
                    created_at,
                ),
            )
            connection.execute(
                """INSERT INTO agent_versions
                (id, workspace_id, agent_id, version, definition_json,
                 definition_sha256, created_at, published_at)
                VALUES (%s, %s, %s, 1, %s, %s, %s, NULL)""",
                (
                    "av_legacy_1",
                    "ws_legacy",
                    "agt_legacy",
                    definition_json,
                    definition_sha256,
                    created_at,
                ),
            )
            connection.execute(
                """INSERT INTO threads
                (id, workspace_id, agent_id, agent_version_id, title, context_json,
                 next_message_sequence, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, '{}', 0, %s, %s)""",
                (
                    "thr_legacy",
                    "ws_legacy",
                    "agt_legacy",
                    "av_legacy_1",
                    "Legacy Thread",
                    created_at,
                    created_at,
                ),
            )

        command.upgrade(config, "head")
        with psycopg.connect(DATABASE_URL) as connection:
            configuration = connection.execute(
                """SELECT thread_id, workspace_id, revision, created_at, updated_at
                FROM thread_configurations WHERE thread_id = %s""",
                ("thr_legacy",),
            ).fetchone()
            empty_preferences = connection.execute(
                "SELECT count(*) FROM workspace_preferences"
            ).fetchone()

        assert configuration == (
            "thr_legacy",
            "ws_legacy",
            0,
            created_at,
            created_at,
        )
        assert empty_preferences == (0,)

        command.downgrade(config, "20260829_0003")
        with psycopg.connect(DATABASE_URL) as connection:
            tables = connection.execute(
                """SELECT to_regclass('public.skills'),
                    to_regclass('public.thread_configurations'),
                    to_regclass('public.workspace_preferences')"""
            ).fetchone()
        assert tables == (None, None, None)
    finally:
        command.upgrade(config, "head")
