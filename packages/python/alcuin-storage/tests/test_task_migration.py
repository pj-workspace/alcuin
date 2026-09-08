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
    raise RuntimeError(
        "Migration and contract URLs must reference the same _test database"
    )

pytestmark = pytest.mark.skipif(
    not DATABASE_URL or not MIGRATION_URL,
    reason="Isolated ALCUIN_TEST_POSTGRES_URL is required for Task migration tests",
)


def test_task_runtime_migration_round_trips_from_artifact_schema() -> None:
    assert DATABASE_URL is not None
    config = Config(str(ROOT / "packages/python/alcuin-storage/alembic.ini"))
    try:
        command.downgrade(config, "20260829_0007")
        command.upgrade(config, "head")
        with psycopg.connect(DATABASE_URL) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    """SELECT tablename FROM pg_tables
                    WHERE schemaname = 'public' AND tablename LIKE 'task%'"""
                ).fetchall()
            }
            constraints = {
                row[0]
                for row in connection.execute(
                    """SELECT conname FROM pg_constraint
                    WHERE conname IN (
                        'tasks_current_plan_workspace_fkey',
                        'tasks_current_step_workspace_fkey',
                        'task_events_task_sequence_key',
                        'task_commands_task_idempotency_key',
                        'task_run_links_run_workspace_key'
                    )"""
                ).fetchall()
            }
            command_columns = {
                row[0]
                for row in connection.execute(
                    """SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'task_commands'"""
                ).fetchall()
            }
            profile_columns = {
                row[0]
                for row in connection.execute(
                    """SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'tasks'
                    AND column_name IN ('model_override', 'reasoning_effort')
                    AND is_nullable = 'YES'"""
                ).fetchall()
            }
        assert tables == {
            "tasks",
            "task_plans",
            "task_steps",
            "task_step_attempts",
            "task_run_links",
            "task_checkpoints",
            "task_events",
            "task_commands",
            "task_interventions",
            "task_dispatch",
        }
        assert constraints == {
            "tasks_current_plan_workspace_fkey",
            "tasks_current_step_workspace_fkey",
            "task_events_task_sequence_key",
            "task_commands_task_idempotency_key",
            "task_run_links_run_workspace_key",
        }
        assert {
            "request_sha256",
            "result_revision",
            "result_status",
            "result_json",
        } <= (command_columns)
        assert profile_columns == {"model_override", "reasoning_effort"}
        command.downgrade(config, "20260830_0008")
        with psycopg.connect(DATABASE_URL) as connection:
            assert (
                connection.execute(
                    """SELECT count(*) FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'tasks'
                AND column_name IN ('model_override', 'reasoning_effort')"""
                ).fetchone()[0]
                == 0
            )
    finally:
        command.upgrade(config, "head")
