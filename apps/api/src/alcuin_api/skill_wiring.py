"""Wire native progressive Skill tools into the runtime without granting permissions."""

from __future__ import annotations

from typing import Any

from alcuin_core.contracts import AgentDefinition
from alcuin_core.tools import ToolContext
from alcuin_customization import (
    BoundSkillProvider,
    BoundSkillSnapshot,
    ParsedSkill,
    SkillBuiltinTools,
    SkillRegistry,
    SkillResource,
    SkillRuntimeContext,
    SkillRuntimeError,
)
from alcuin_storage import ControlPlaneRepository

from .tools import ToolDefinition, ToolError


def _parsed_skill(version: dict[str, Any]) -> ParsedSkill:
    definition = version.get("definition")
    if not isinstance(definition, dict):
        raise TypeError("Skill definition is unavailable")
    resources = tuple(
        SkillResource(
            path=str(resource["path"]),
            kind=str(resource["kind"]),
            media_type=str(resource["media_type"]),
            size=int(resource["size"]),
            digest=str(resource["digest"]),
            content=(
                str(resource["content"])
                if resource.get("content") is not None
                else None
            ),
        )
        for resource in definition.get("resources") or []
        if isinstance(resource, dict)
    )
    return ParsedSkill(
        name=str(definition["name"]),
        description=str(definition["description"]),
        instructions=str(definition["instructions"]),
        disable_model_invocation=bool(
            definition.get("disable_model_invocation", False)
        ),
        user_invocable=bool(definition.get("user_invocable", True)),
        paths=tuple(str(item) for item in definition.get("paths") or []),
        metadata=(
            dict(definition.get("metadata") or {})
            if isinstance(definition.get("metadata") or {}, dict)
            else {}
        ),
        resources=resources,
        source_path=str(version.get("source_ref") or "SKILL.md"),
        content_digest=str(version.get("definition_sha256") or ""),
    )


class RepositoryBoundSkillProvider(BoundSkillProvider):
    """Resolve only enabled Skill versions bound to one Workspace-owned Agent version."""

    def __init__(self, repository: ControlPlaneRepository) -> None:
        self.repository = repository

    def _snapshot(
        self,
        *,
        workspace_id: str,
        agent_version_id: str,
        version: dict[str, Any],
    ) -> BoundSkillSnapshot | None:
        version_id = str(version.get("id") or "")
        if (
            str(version.get("workspace_id") or "") != workspace_id
            or str(version.get("agent_version_id") or "") != agent_version_id
            or version.get("enabled") is not True
        ):
            return None
        return BoundSkillSnapshot(
            workspace_id=workspace_id,
            agent_version_id=agent_version_id,
            skill_version_id=version_id,
            skill=_parsed_skill(version),
            invocation_mode=str(version.get("mode") or "auto"),  # type: ignore[arg-type]
            enabled=True,
        )

    def list_bound_skill_versions(
        self,
        *,
        workspace_id: str,
        agent_version_id: str,
    ) -> tuple[BoundSkillSnapshot, ...]:
        versions = self.repository.list_bound_skill_versions(
            workspace_id,
            agent_version_id,
            enabled_only=True,
        )
        snapshots = [
            snapshot
            for version in versions
            if isinstance(version, dict)
            and (
                snapshot := self._snapshot(
                    workspace_id=workspace_id,
                    agent_version_id=agent_version_id,
                    version=version,
                )
            )
            is not None
        ]
        return tuple(snapshots)

    def get_bound_skill_version(
        self,
        *,
        workspace_id: str,
        agent_version_id: str,
        skill_version_id: str,
    ) -> BoundSkillSnapshot | None:
        version = self.repository.get_bound_skill_version(
            workspace_id,
            agent_version_id,
            skill_version_id,
        )
        if version is None:
            return None
        return self._snapshot(
            workspace_id=workspace_id,
            agent_version_id=agent_version_id,
            version=version,
        )


def _runtime_context(
    repository: ControlPlaneRepository,
    context: ToolContext,
) -> SkillRuntimeContext:
    run = repository.get_run(context.workspace_id, context.run_id)
    if run is None:
        raise ToolError(
            "skill_not_available", "Agent Skills are unavailable for this run."
        )
    version_id = str(run["agent_version_id"])
    version = repository.get_agent_version(context.workspace_id, version_id)
    if version is None:
        raise ToolError(
            "skill_not_available", "Agent Skills are unavailable for this run."
        )
    definition = AgentDefinition.model_validate(version["definition"])
    assembly = repository.get_context_assembly(context.workspace_id, context.run_id)
    active_ids = frozenset(
        str(entry.get("source_id"))
        for entry in (assembly or {}).get("entries") or []
        if isinstance(entry, dict)
        and str(entry.get("id") or "").startswith("skill-version:")
        and entry.get("source_id")
    )
    return SkillRuntimeContext(
        workspace_id=context.workspace_id,
        agent_version_id=version_id,
        active_skill_version_ids=active_ids,
        agent_tool_allow_list=frozenset(definition.tools),
    )


def skill_tool_definitions(
    repository: ControlPlaneRepository,
) -> tuple[ToolDefinition, ...]:
    builtins = SkillBuiltinTools(
        SkillRegistry(RepositoryBoundSkillProvider(repository))
    )
    definitions: list[ToolDefinition] = []
    for contract in builtins.definitions():

        async def execute(
            context: ToolContext,
            arguments: dict[str, Any],
            *,
            tool_name: str = contract.name,
        ):
            try:
                return builtins.execute(
                    tool_name,
                    arguments,
                    context=_runtime_context(repository, context),
                )
            except SkillRuntimeError as exc:
                raise ToolError(exc.code, exc.message) from exc

        definitions.append(
            ToolDefinition(
                name=contract.name,
                description=contract.description,
                input_schema=contract.input_schema,
                handler=execute,
                mutating=False,
                timeout_seconds=2.0,
                max_calls_per_run=8,
            )
        )
    return tuple(definitions)
