"""Public persistence ports and adapters for Alcuin."""

from .errors import (
    ArtifactVersionConflict,
    AttachmentBindingError,
    RepositoryConflict,
    RepositoryError,
    TaskRevisionConflict,
    TaskTransitionConflict,
)
from .factory import open_repository
from .memory_attachments import InMemoryAttachmentRepository
from .postgres import PostgresStore
from .repositories import (
    ConversationRepository,
    AttachmentRepository,
    ArtifactRepository,
    ControlPlaneRepository,
    CustomizationRepository,
    ExtensionRepository,
    KnowledgeRepository,
    RuleSummary,
    RuntimeRepository,
    SkillSummary,
    TaskRepository,
)

__all__ = [
    "AttachmentBindingError",
    "AttachmentRepository",
    "ArtifactRepository",
    "ArtifactVersionConflict",
    "ControlPlaneRepository",
    "ConversationRepository",
    "CustomizationRepository",
    "ExtensionRepository",
    "KnowledgeRepository",
    "InMemoryAttachmentRepository",
    "RepositoryConflict",
    "RepositoryError",
    "RuleSummary",
    "RuntimeRepository",
    "SkillSummary",
    "TaskRepository",
    "TaskRevisionConflict",
    "TaskTransitionConflict",
    "PostgresStore",
    "open_repository",
]
