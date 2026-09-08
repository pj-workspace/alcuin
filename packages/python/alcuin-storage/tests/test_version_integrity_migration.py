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
    database_name = urlsplit(url).path.removeprefix("/")
    if not database_name.endswith("_test"):
        raise RuntimeError(
            "Refusing to migrate a PostgreSQL database whose name does not end in '_test'"
        )
    return url


DATABASE_URL = require_isolated_test_database(DATABASE_URL)
MIGRATION_URL = require_isolated_test_database(MIGRATION_URL)
if DATABASE_URL and MIGRATION_URL and DATABASE_URL != MIGRATION_URL:
    raise RuntimeError(
        "Migration and contract test URLs must reference the same _test database"
    )

pytestmark = pytest.mark.skipif(
    not DATABASE_URL or not MIGRATION_URL,
    reason="Isolated ALCUIN_TEST_POSTGRES_URL is required for migration tests",
)


def alembic_config() -> Config:
    return Config(str(ROOT / "packages/python/alcuin-storage/alembic.ini"))


def test_version_integrity_migration_backfills_legacy_records() -> None:
    assert DATABASE_URL is not None
    config = alembic_config()
    definition_json = '{"identity":{"name":"Legacy Agent"}}'
    created_at = "2026-08-28T00:00:00+00:00"
    try:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                """TRUNCATE TABLE
                run_context_assemblies, thread_compactions, messages,
                knowledge_documents, knowledge_sources, extensions, approvals, events,
                runs, threads, agent_versions, agents, workspaces
                CASCADE"""
            )
        command.downgrade(config, "20260829_0002")
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "INSERT INTO workspaces(id, name, created_at) VALUES (%s, %s, %s)",
                ("ws_legacy", "Legacy Workspace", created_at),
            )
            connection.execute(
                """INSERT INTO agents
                (id, workspace_id, slug, name, description, status,
                 current_version_id, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    "agt_legacy",
                    "ws_legacy",
                    "legacy-agent",
                    "Legacy Agent",
                    "Migration fixture",
                    "published",
                    "av_legacy_2",
                    created_at,
                ),
            )
            for version_id, version in (("av_legacy_1", 1), ("av_legacy_2", 2)):
                connection.execute(
                    """INSERT INTO agent_versions
                    (id, workspace_id, agent_id, version, definition_json, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)""",
                    (
                        version_id,
                        "ws_legacy",
                        "agt_legacy",
                        version,
                        definition_json,
                        created_at,
                    ),
                )
            for thread_id in ("thr_with_runs", "thr_without_runs"):
                connection.execute(
                    """INSERT INTO threads
                    (id, workspace_id, agent_id, title, context_json, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (
                        thread_id,
                        "ws_legacy",
                        "agt_legacy",
                        thread_id,
                        "{}",
                        created_at,
                        created_at,
                    ),
                )
            connection.execute(
                """INSERT INTO runs
                (id, workspace_id, thread_id, agent_version_id, status, input, created_at)
                VALUES
                (%s, %s, %s, %s, %s, %s, %s),
                (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    "run_legacy_1",
                    "ws_legacy",
                    "thr_with_runs",
                    "av_legacy_1",
                    "completed",
                    "first",
                    "2026-08-28T00:01:00+00:00",
                    "run_legacy_2",
                    "ws_legacy",
                    "thr_with_runs",
                    "av_legacy_2",
                    "completed",
                    "second",
                    "2026-08-28T00:02:00+00:00",
                ),
            )

        command.upgrade(config, "head")

        with psycopg.connect(DATABASE_URL) as connection:
            agent = connection.execute(
                """SELECT published_version_id, created_at, updated_at
                FROM agents WHERE id = %s""",
                ("agt_legacy",),
            ).fetchone()
            versions = connection.execute(
                """SELECT id, definition_sha256, published_at
                FROM agent_versions WHERE agent_id = %s ORDER BY version""",
                ("agt_legacy",),
            ).fetchall()
            threads = dict(
                connection.execute(
                    """SELECT id, agent_version_id FROM threads
                    WHERE agent_id = %s ORDER BY id""",
                    ("agt_legacy",),
                ).fetchall()
            )

        assert agent == ("av_legacy_2", created_at, created_at)
        expected_hash = hashlib.sha256(definition_json.encode("utf-8")).hexdigest()
        assert versions == [
            ("av_legacy_1", expected_hash, None),
            ("av_legacy_2", expected_hash, created_at),
        ]
        assert threads == {
            "thr_with_runs": "av_legacy_2",
            "thr_without_runs": "av_legacy_2",
        }
    finally:
        command.upgrade(config, "head")
