"""Isolated PostgreSQL fixtures shared by API tests."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import psycopg

from alcuin_storage import PostgresStore


def test_database_url() -> str:
    url = os.environ.get("ALCUIN_TEST_POSTGRES_URL")
    if not url:
        raise RuntimeError(
            "ALCUIN_TEST_POSTGRES_URL is required; start the test PostgreSQL service first"
        )
    database_name = urlsplit(url).path.removeprefix("/")
    if not database_name.endswith("_test"):
        raise RuntimeError(
            "Refusing to reset a PostgreSQL database whose name does not end in '_test'"
        )
    return url


def reset_test_database() -> None:
    with psycopg.connect(test_database_url()) as connection:
        connection.execute(
            """TRUNCATE TABLE
            artifact_versions, artifacts,
            message_attachments, attachment_blobs, attachments,
            run_context_assemblies, thread_compactions, messages,
            knowledge_documents, knowledge_sources, extensions, approvals, events,
            runs, threads, agent_versions, agents, workspaces
            CASCADE"""
        )


def create_test_store() -> PostgresStore:
    """Return a clean PostgreSQL Store."""
    reset_test_database()
    return PostgresStore(test_database_url(), pool_max_size=4)
