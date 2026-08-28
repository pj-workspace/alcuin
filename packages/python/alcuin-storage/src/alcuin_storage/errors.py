"""Adapter-neutral persistence failures safe for service-layer handling."""


class RepositoryError(Exception):
    """Base error raised by a persistence adapter."""


class RepositoryConflict(RepositoryError, ValueError):
    """A scoped uniqueness or state invariant rejected a write."""
