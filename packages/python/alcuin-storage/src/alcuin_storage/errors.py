"""Adapter-neutral persistence failures safe for service-layer handling."""


class RepositoryError(Exception):
    """Base error raised by a persistence adapter."""


class RepositoryConflict(RepositoryError, ValueError):
    """A scoped uniqueness or state invariant rejected a write."""


class AttachmentBindingError(RepositoryConflict):
    """A staged Attachment could not be atomically bound to a new user message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ArtifactVersionConflict(RepositoryConflict):
    """An Artifact edit targeted a stale optimistic-lock version."""

    def __init__(self, current_version: int) -> None:
        super().__init__(
            "Artifact changed since it was opened; refresh before saving"
        )
        self.current_version = current_version


class TaskRevisionConflict(RepositoryConflict):
    """A Task command targeted a stale internal compare-and-swap revision."""

    def __init__(self, current_revision: int, current_status: str) -> None:
        super().__init__(
            "Task changed while the command was in flight; reload before retrying"
        )
        self.current_revision = current_revision
        self.current_status = current_status


class TaskTransitionConflict(RepositoryConflict):
    """A named Task command is invalid from the durable current state."""

    def __init__(self, command: str, current_status: str) -> None:
        super().__init__(f"Cannot {command} a Task in status {current_status}")
        self.command = command
        self.current_status = current_status
