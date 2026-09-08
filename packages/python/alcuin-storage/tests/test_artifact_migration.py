from __future__ import annotations

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


def require_test_database(url: str | None) -> str | None:
    if url is None:
        return None
    if not urlsplit(url).path.removeprefix("/").endswith("_test"):
        raise RuntimeError(
            "Refusing to migrate a PostgreSQL database whose name does not end in '_test'"
        )
    return url


DATABASE_URL = require_test_database(DATABASE_URL)
MIGRATION_URL = require_test_database(MIGRATION_URL)
if DATABASE_URL and MIGRATION_URL and DATABASE_URL != MIGRATION_URL:
    raise RuntimeError("Migration and contract URLs must reference the same _test database")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL or not MIGRATION_URL,
    reason="Isolated ALCUIN_TEST_POSTGRES_URL is required for Artifact migration tests",
)


def test_artifact_migration_round_trips_from_attachment_schema() -> None:
    assert DATABASE_URL is not None
    config = Config(str(ROOT / "packages/python/alcuin-storage/alembic.ini"))
    try:
        command.downgrade(config, "20260829_0006")
        command.upgrade(config, "head")
        with psycopg.connect(DATABASE_URL) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    """SELECT tablename FROM pg_tables
                    WHERE schemaname = 'public'
                      AND tablename IN ('artifacts', 'artifact_versions')"""
                ).fetchall()
            }
            version_columns = {
                row[0]
                for row in connection.execute(
                    """SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'artifact_versions'"""
                ).fetchall()
            }
            constraints = {
                row[0]
                for row in connection.execute(
                    """SELECT conname FROM pg_constraint
                    WHERE conname IN (
                        'threads_id_workspace_key',
                        'runs_id_workspace_key',
                        'runs_id_thread_workspace_key'
                    )"""
                ).fetchall()
            }
        assert tables == {"artifacts", "artifact_versions"}
        assert {"kind", "content_type"}.issubset(version_columns)
        assert constraints == {
            "threads_id_workspace_key",
            "runs_id_workspace_key",
            "runs_id_thread_workspace_key",
        }
    finally:
        command.upgrade(config, "head")
