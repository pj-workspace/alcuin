"""Pooled PostgreSQL adapter implementing the Alcuin persistence ports."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .errors import RepositoryError
from .repository import SqlRepository


@dataclass
class _BufferedResult:
    rows: list[Mapping[str, Any]]
    rowcount: int

    def fetchone(self) -> Mapping[str, Any] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[Mapping[str, Any]]:
        return self.rows


class _PooledConnection:
    """Small DB-API compatibility facade used by the shared repository behavior."""

    def __init__(
        self,
        database_url: str,
        *,
        min_size: int,
        max_size: int,
        timeout: float,
    ) -> None:
        self.pool = ConnectionPool(
            conninfo=database_url,
            min_size=min_size,
            max_size=max_size,
            timeout=timeout,
            kwargs={"row_factory": dict_row},
            open=False,
        )
        self.pool.open(wait=True, timeout=timeout)
        self.local = threading.local()

    def __enter__(self) -> "_PooledConnection":
        if getattr(self.local, "context", None) is not None:
            raise RepositoryError("Nested repository transactions are not supported")
        context = self.pool.connection()
        connection = context.__enter__()
        self.local.context = context
        self.local.connection = connection
        return self

    def __exit__(self, error_type: object, error: object, traceback: object) -> bool:
        context = self.local.context
        try:
            return bool(context.__exit__(error_type, error, traceback))
        finally:
            self.local.context = None
            self.local.connection = None

    @staticmethod
    def _postgres_query(query: str) -> str:
        return query.replace("?", "%s")

    def _execute_on(
        self,
        connection: Connection[dict[str, Any]],
        query: str,
        params: tuple[Any, ...],
    ) -> _BufferedResult:
        cursor = connection.execute(self._postgres_query(query), params)
        rows = list(cursor.fetchall()) if cursor.description is not None else []
        return _BufferedResult(rows=rows, rowcount=cursor.rowcount)

    def execute(
        self,
        query: str,
        params: tuple[Any, ...] = (),
    ) -> _BufferedResult:
        active = getattr(self.local, "connection", None)
        if active is not None:
            return self._execute_on(active, query, params)
        with self.pool.connection() as connection:
            return self._execute_on(connection, query, params)

    def close(self) -> None:
        self.pool.close()


class PostgresStore(SqlRepository):
    """PostgreSQL implementation sharing the verified repository behavior."""

    def __init__(
        self,
        database_url: str,
        *,
        pool_min_size: int = 1,
        pool_max_size: int = 10,
        pool_timeout_seconds: float = 10.0,
    ) -> None:
        if pool_min_size < 1 or pool_max_size < pool_min_size:
            raise ValueError("Invalid PostgreSQL pool size")
        self.connection = _PooledConnection(
            database_url,
            min_size=pool_min_size,
            max_size=pool_max_size,
            timeout=pool_timeout_seconds,
        )
        self.lock = threading.RLock()
        self.initialize()

    def initialize(self) -> None:
        row = self._one("SELECT to_regclass('public.workspaces') AS name", ())
        if row is None or row["name"] is None:
            self.close()
            raise RepositoryError(
                "PostgreSQL schema is not initialized; run Alembic upgrade head"
            )
        self.seed_starter()

    @staticmethod
    def _is_unique_violation(
        error: Exception,
        *,
        postgres_constraint: str,
    ) -> bool:
        diagnostic = getattr(error, "diag", None)
        return getattr(diagnostic, "constraint_name", None) == postgres_constraint
