"""Repository construction without leaking adapter selection into services."""

from __future__ import annotations

from urllib.parse import urlsplit

from .errors import RepositoryError
from .postgres import PostgresStore
from .repositories import ControlPlaneRepository


def open_repository(
    *,
    database_url: str,
    postgres_pool_min_size: int = 1,
    postgres_pool_max_size: int = 10,
    postgres_pool_timeout_seconds: float = 10.0,
) -> ControlPlaneRepository:
    scheme = urlsplit(database_url).scheme.casefold()
    if scheme not in {"postgres", "postgresql"}:
        raise RepositoryError(f"Unsupported database URL scheme: {scheme or 'missing'}")

    return PostgresStore(
        database_url,
        pool_min_size=postgres_pool_min_size,
        pool_max_size=postgres_pool_max_size,
        pool_timeout_seconds=postgres_pool_timeout_seconds,
    )
