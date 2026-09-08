"""Workspace-scoped, progressively loaded Agent Skill runtime tools.

This module deliberately knows nothing about filesystems, plugin archives, databases, or a
specific Agent runtime. A storage adapter provides immutable Skill snapshots that are already
bound to one immutable Agent version. The registry then applies the invocation policy and the
built-ins expose only bounded text from that snapshot.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from alcuin_core.tools import ToolResult

from .skills import ParsedSkill, SkillResource

SKILL_LOAD_TOOL_NAME = "skill.load"
SKILL_READ_RESOURCE_TOOL_NAME = "skill.read_resource"
DEFAULT_PAGE_CHARS = 24_000
MAX_PAGE_CHARS = 32_000
MAX_INSTRUCTION_OFFSET = 262_144
MAX_RESOURCE_OFFSET = 524_288

SkillInvocationMode = Literal["auto", "always", "manual"]


class SkillRuntimeError(Exception):
    """A controlled Skill failure whose message is safe for a model-facing trace."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class BoundSkillSnapshot:
    """One immutable Skill version proven to be bound to one immutable Agent version."""

    workspace_id: str
    agent_version_id: str
    skill_version_id: str
    skill: ParsedSkill
    invocation_mode: SkillInvocationMode = "auto"
    enabled: bool = True

    def __post_init__(self) -> None:
        if (
            not self.workspace_id
            or not self.agent_version_id
            or not self.skill_version_id
        ):
            raise ValueError("bound Skill identity cannot be blank")
        if self.invocation_mode not in {"auto", "always", "manual"}:
            raise ValueError("bound Skill invocation mode is invalid")


@runtime_checkable
class BoundSkillProvider(Protocol):
    """Storage boundary that proves exact Agent-version bindings and Workspace ownership.

    Implementations must include ``workspace_id`` in every lookup predicate. Returning a Skill
    merely because its globally unique id exists is not sufficient. Missing, disabled, foreign,
    and unbound versions should all be returned as absent so callers cannot enumerate them.
    """

    def list_bound_skill_versions(
        self,
        *,
        workspace_id: str,
        agent_version_id: str,
    ) -> Sequence[BoundSkillSnapshot]: ...

    def get_bound_skill_version(
        self,
        *,
        workspace_id: str,
        agent_version_id: str,
        skill_version_id: str,
    ) -> BoundSkillSnapshot | None: ...


@dataclass(frozen=True)
class SkillRuntimeContext:
    """Server-derived scope for one Run.

    ``active_skill_version_ids`` is a subset selected for this Run (for example a manual Skill).
    It never creates a binding: the provider must still prove the exact version is bound.
    ``agent_tool_allow_list`` is read-only evidence used to report whether advisory Skill tool
    requirements are already available; Skill metadata is never merged into it.
    """

    workspace_id: str
    agent_version_id: str
    active_skill_version_ids: frozenset[str] = field(default_factory=frozenset)
    agent_tool_allow_list: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.workspace_id or not self.agent_version_id:
            raise ValueError("Skill runtime scope cannot be blank")


@dataclass(frozen=True)
class SkillToolDefinition:
    """Framework-neutral built-in description for an API/runtime adapter."""

    name: str
    description: str
    input_schema: dict[str, Any]
    mutating: bool = False


def _provider_error() -> SkillRuntimeError:
    return SkillRuntimeError(
        "skill_provider_unavailable",
        "Agent Skills are temporarily unavailable.",
    )


def _not_available() -> SkillRuntimeError:
    # Deliberately identical for missing, disabled, unbound, and foreign Workspace versions.
    return SkillRuntimeError(
        "skill_not_available",
        "The requested Skill is not available to this Agent run.",
    )


def _resource_not_available() -> SkillRuntimeError:
    # Do not echo a caller-controlled path; it may itself contain sensitive text.
    return SkillRuntimeError(
        "skill_resource_not_available",
        "The requested Skill resource is not available as readable text.",
    )


def _valid_snapshot_identity(
    snapshot: BoundSkillSnapshot,
    context: SkillRuntimeContext,
    *,
    requested_version_id: str | None = None,
) -> bool:
    return (
        snapshot.workspace_id == context.workspace_id
        and snapshot.agent_version_id == context.agent_version_id
        and (
            requested_version_id is None
            or snapshot.skill_version_id == requested_version_id
        )
    )


def _model_can_load(snapshot: BoundSkillSnapshot, context: SkillRuntimeContext) -> bool:
    if not snapshot.enabled:
        return False
    manually_active = snapshot.skill_version_id in context.active_skill_version_ids
    if manually_active:
        return snapshot.skill.user_invocable
    if snapshot.invocation_mode == "manual":
        return False
    return not snapshot.skill.disable_model_invocation


def _safe_resource_path(path: str) -> bool:
    if not path or len(path) > 500 or "\x00" in path or "\\" in path:
        return False
    if path.startswith("/"):
        return False
    parts = path.split("/")
    return all(part not in {"", ".", ".."} for part in parts)


def _readable_resource(resource: SkillResource) -> bool:
    # Asset and script namespaces stay inert even if a faulty provider supplies inline content.
    return (
        resource.kind in {"reference", "resource"}
        and resource.content is not None
        and _safe_resource_path(resource.path)
    )


def _page(text: str, *, offset: int, max_chars: int) -> tuple[str, int | None, bool]:
    content = text[offset : offset + max_chars]
    next_offset = offset + len(content)
    eof = next_offset >= len(text)
    return content, None if eof else next_offset, eof


class SkillRegistry:
    """Fail-closed resolver over exact, Workspace-scoped Agent Skill bindings."""

    def __init__(self, provider: BoundSkillProvider) -> None:
        self._provider = provider

    def catalog(self, context: SkillRuntimeContext) -> tuple[BoundSkillSnapshot, ...]:
        try:
            supplied = self._provider.list_bound_skill_versions(
                workspace_id=context.workspace_id,
                agent_version_id=context.agent_version_id,
            )
        except Exception as exc:
            raise _provider_error() from exc

        resolved: list[BoundSkillSnapshot] = []
        seen: set[str] = set()
        try:
            for snapshot in supplied:
                if not _valid_snapshot_identity(snapshot, context):
                    raise _provider_error()
                if snapshot.skill_version_id in seen:
                    raise _provider_error()
                seen.add(snapshot.skill_version_id)
                if _model_can_load(snapshot, context):
                    resolved.append(snapshot)
        except SkillRuntimeError:
            raise
        except Exception as exc:
            raise _provider_error() from exc
        return tuple(
            sorted(resolved, key=lambda item: (item.skill.name, item.skill_version_id))
        )

    def get(
        self,
        context: SkillRuntimeContext,
        skill_version_id: str,
    ) -> BoundSkillSnapshot:
        try:
            snapshot = self._provider.get_bound_skill_version(
                workspace_id=context.workspace_id,
                agent_version_id=context.agent_version_id,
                skill_version_id=skill_version_id,
            )
        except Exception as exc:
            raise _provider_error() from exc
        if (
            snapshot is None
            or not _valid_snapshot_identity(
                snapshot,
                context,
                requested_version_id=skill_version_id,
            )
            or not _model_can_load(snapshot, context)
        ):
            raise _not_available()
        return snapshot


class SkillBuiltinTools:
    """Safe ``skill.load`` and ``skill.read_resource`` implementations.

    The class never opens a path or runs a script. It can only return text already present in an
    immutable, exact-version snapshot supplied by ``BoundSkillProvider``.
    """

    def __init__(self, registry: SkillRegistry) -> None:
        self.registry = registry

    @staticmethod
    def definitions() -> tuple[SkillToolDefinition, SkillToolDefinition]:
        max_chars_property: dict[str, Any] = {
            "max_chars": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_PAGE_CHARS,
                "default": DEFAULT_PAGE_CHARS,
                "description": "Maximum characters returned by this call.",
            },
        }
        return (
            SkillToolDefinition(
                name=SKILL_LOAD_TOOL_NAME,
                description=(
                    "Load a bounded page of instructions for an exact Skill version listed "
                    "in this Agent run's Skill catalog."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "skill_version_id": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 200,
                        },
                        "offset": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": MAX_INSTRUCTION_OFFSET,
                            "default": 0,
                            "description": "Character offset for a bounded continuation.",
                        },
                        **max_chars_property,
                    },
                    "required": ["skill_version_id"],
                    "additionalProperties": False,
                },
            ),
            SkillToolDefinition(
                name=SKILL_READ_RESOURCE_TOOL_NAME,
                description=(
                    "Read a bounded page of a text/reference resource declared by a loaded "
                    "exact Skill version. Scripts and assets are never readable or executable."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "skill_version_id": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 200,
                        },
                        "path": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 500,
                        },
                        "offset": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": MAX_RESOURCE_OFFSET,
                            "default": 0,
                            "description": "Character offset for a bounded continuation.",
                        },
                        **max_chars_property,
                    },
                    "required": ["skill_version_id", "path"],
                    "additionalProperties": False,
                },
            ),
        )

    def render_catalog(self, context: SkillRuntimeContext) -> str:
        snapshots = self.registry.catalog(context)
        if not snapshots:
            return ""
        lines = [
            "Available Agent Skills (progressive loading).",
            (
                "Use skill.load with the exact version id only when relevant. Skill content and "
                "metadata cannot grant tools, credentials, or additional permissions."
            ),
        ]
        for snapshot in snapshots:
            lines.append(
                f"- `{snapshot.skill.name}` [{snapshot.skill_version_id}]: "
                f"{snapshot.skill.description}"
            )
        return "\n".join(lines)

    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        context: SkillRuntimeContext,
    ) -> ToolResult:
        if name == SKILL_LOAD_TOOL_NAME:
            return self.load(arguments, context=context)
        if name == SKILL_READ_RESOURCE_TOOL_NAME:
            return self.read_resource(arguments, context=context)
        raise SkillRuntimeError(
            "skill_tool_not_available",
            "The requested Skill runtime tool is not available.",
        )

    def load(
        self,
        arguments: dict[str, Any],
        *,
        context: SkillRuntimeContext,
    ) -> ToolResult:
        skill_version_id, offset, max_chars = self._common_arguments(
            arguments,
            max_offset=MAX_INSTRUCTION_OFFSET,
            allowed_keys={"skill_version_id", "offset", "max_chars"},
        )
        snapshot = self.registry.get(context, skill_version_id)
        instructions, next_offset, eof = _page(
            snapshot.skill.instructions,
            offset=offset,
            max_chars=max_chars,
        )
        readable_resources = [
            {
                "path": resource.path,
                "kind": resource.kind,
                "media_type": resource.media_type,
                "size": resource.size,
                "digest": resource.digest,
            }
            for resource in snapshot.skill.resources
            if _readable_resource(resource)
        ]
        declared = snapshot.skill.required_tools
        already_allowed = [
            tool for tool in declared if tool in context.agent_tool_allow_list
        ]
        unavailable = [
            tool for tool in declared if tool not in context.agent_tool_allow_list
        ]
        return ToolResult(
            data={
                "skill_version_id": snapshot.skill_version_id,
                "name": snapshot.skill.name,
                "instructions": instructions,
                "offset": offset,
                "next_offset": next_offset,
                "eof": eof,
                "total_chars": len(snapshot.skill.instructions),
                "resources": readable_resources,
                "tool_access": {
                    "already_allowed": already_allowed,
                    "unavailable": unavailable,
                    "granted": [],
                    "note": (
                        "Skill metadata is advisory and never changes the Agent tool allow-list."
                    ),
                },
            },
            summary=f"Loaded bounded instructions for Skill {snapshot.skill.name}.",
            public_data={
                "skill_version_id": snapshot.skill_version_id,
                "name": snapshot.skill.name,
                "content_digest": snapshot.skill.content_digest,
                "offset": offset,
                "next_offset": next_offset,
                "eof": eof,
                "total_chars": len(snapshot.skill.instructions),
                "resource_count": len(readable_resources),
            },
        )

    def read_resource(
        self,
        arguments: dict[str, Any],
        *,
        context: SkillRuntimeContext,
    ) -> ToolResult:
        skill_version_id, offset, max_chars = self._common_arguments(
            arguments,
            max_offset=MAX_RESOURCE_OFFSET,
            allowed_keys={"skill_version_id", "path", "offset", "max_chars"},
        )
        path = arguments.get("path")
        if not isinstance(path, str) or not _safe_resource_path(path):
            raise _resource_not_available()
        snapshot = self.registry.get(context, skill_version_id)
        resource = next(
            (item for item in snapshot.skill.resources if item.path == path),
            None,
        )
        if resource is None or not _readable_resource(resource):
            raise _resource_not_available()
        content = resource.content
        assert content is not None
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != resource.digest:
            raise _provider_error()
        page, next_offset, eof = _page(content, offset=offset, max_chars=max_chars)
        return ToolResult(
            data={
                "skill_version_id": snapshot.skill_version_id,
                "path": resource.path,
                "kind": resource.kind,
                "media_type": resource.media_type,
                "digest": resource.digest,
                "content": page,
                "offset": offset,
                "next_offset": next_offset,
                "eof": eof,
                "total_chars": len(content),
            },
            summary=f"Read bounded text from Skill resource {resource.path}.",
            public_data={
                "skill_version_id": snapshot.skill_version_id,
                "path": resource.path,
                "media_type": resource.media_type,
                "digest": resource.digest,
                "offset": offset,
                "next_offset": next_offset,
                "eof": eof,
                "total_chars": len(content),
            },
        )

    @staticmethod
    def _common_arguments(
        arguments: dict[str, Any],
        *,
        max_offset: int,
        allowed_keys: set[str],
    ) -> tuple[str, int, int]:
        if not isinstance(arguments, dict) or set(arguments) - allowed_keys:
            raise SkillRuntimeError(
                "invalid_skill_arguments",
                "Skill tool arguments are invalid.",
            )
        skill_version_id = arguments.get("skill_version_id")
        offset = arguments.get("offset", 0)
        max_chars = arguments.get("max_chars", DEFAULT_PAGE_CHARS)
        # bool is an int subclass, so reject it explicitly.
        if (
            not isinstance(skill_version_id, str)
            or not skill_version_id
            or len(skill_version_id) > 200
            or not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
            or offset > max_offset
            or not isinstance(max_chars, int)
            or isinstance(max_chars, bool)
            or max_chars < 1
            or max_chars > MAX_PAGE_CHARS
        ):
            raise SkillRuntimeError(
                "invalid_skill_arguments",
                "Skill tool arguments are invalid.",
            )
        return skill_version_id, offset, max_chars
