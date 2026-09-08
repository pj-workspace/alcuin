"""Resolve authorized Rules, preferences, and Skills for one Agent turn.

Storage owns Workspace isolation and immutable resource lookup.  This module owns the
deterministic activation semantics that turn those records into provider-neutral context
sections.  Imported content is data until it passes through this boundary; it never grants
tools, permissions, or code execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from alcuin_context import ContextLayer, ContextSection
from alcuin_customization import (
    ParsedRule,
    RuleActivation,
    RuleResolutionInput,
    rule_matches,
)
from alcuin_core.contracts import AgentDefinition


class CustomizationContextRepository(Protocol):
    def get_workspace_preferences(self, workspace_id: str) -> dict[str, Any]: ...

    def get_thread_configuration(
        self, workspace_id: str, thread_id: str
    ) -> dict[str, Any]: ...

    def get_agent_version_customization_bindings(
        self, workspace_id: str, agent_version_id: str
    ) -> dict[str, Any]: ...

    def list_rules(
        self,
        workspace_id: str,
        *,
        scope: str | None = None,
        thread_id: str | None = None,
        enabled_only: bool = False,
    ) -> list[dict[str, Any]]: ...

    def get_rule(
        self, workspace_id: str, rule_id: str
    ) -> dict[str, Any] | None: ...

    def get_rule_version(
        self, workspace_id: str, version_id: str
    ) -> dict[str, Any] | None: ...

    def get_skill(
        self, workspace_id: str, skill_id: str
    ) -> dict[str, Any] | None: ...

    def get_skill_version(
        self, workspace_id: str, version_id: str
    ) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class ResolvedCustomization:
    sections: tuple[ContextSection, ...]
    thread_configuration_revision: int
    workspace_preferences_revision: int | None
    active_skill_version_ids: tuple[str, ...]
    applied_rule_version_ids: tuple[str, ...]


def _definition(record: dict[str, Any], resource: str) -> dict[str, Any]:
    definition = record.get("definition")
    if not isinstance(definition, dict):
        raise RuntimeError(f"{resource} definition is unavailable")
    return definition


def _version_label(record: dict[str, Any]) -> str:
    digest = str(record.get("definition_sha256") or "")
    if digest:
        return digest
    version = record.get("version")
    return str(version) if version is not None else "unknown"


def _parsed_rule(record: dict[str, Any]) -> ParsedRule:
    definition = _definition(record, "Rule")
    activation = RuleActivation(str(definition.get("activation") or "always"))
    conditions = definition.get("conditions") or {}
    if not isinstance(conditions, dict):
        conditions = {}
    return ParsedRule(
        name=str(definition.get("name") or record["id"]),
        description=str(definition.get("description") or ""),
        content=str(definition.get("content") or ""),
        activation=activation,
        conditions=conditions,
        priority=int(definition.get("priority") or 100),
        content_digest=str(record.get("definition_sha256") or ""),
    )


def _current_rule_versions(
    repository: CustomizationContextRepository,
    workspace_id: str,
    *,
    scope: str,
    thread_id: str | None = None,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    resolved: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for identity in repository.list_rules(
        workspace_id,
        scope=scope,
        thread_id=thread_id,
        enabled_only=True,
    ):
        version_id = str(identity.get("current_version_id") or "")
        version = repository.get_rule_version(workspace_id, version_id)
        if version is not None:
            resolved.append((identity, version))
    return resolved


def _bound_rule_versions(
    repository: CustomizationContextRepository,
    workspace_id: str,
    bindings: dict[str, Any],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    resolved: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for binding in bindings.get("rules") or []:
        if not isinstance(binding, dict):
            continue
        version = repository.get_rule_version(
            workspace_id, str(binding.get("rule_version_id") or "")
        )
        if version is None:
            continue
        identity = repository.get_rule(workspace_id, str(version.get("rule_id") or ""))
        if identity is not None and identity.get("enabled") is True:
            resolved.append((identity, version))
    return resolved


def _rule_sections(
    records: list[tuple[dict[str, Any], dict[str, Any], ContextLayer]],
    inputs: RuleResolutionInput,
) -> tuple[list[ContextSection], tuple[str, ...]]:
    candidates: list[
        tuple[int, str, dict[str, Any], dict[str, Any], ContextLayer, ParsedRule]
    ] = []
    seen: set[str] = set()
    for identity, version, layer in records:
        version_id = str(version["id"])
        if version_id in seen:
            continue
        seen.add(version_id)
        parsed = _parsed_rule(version)
        matches = (
            version_id in inputs.manually_selected
            if parsed.activation == RuleActivation.MANUAL
            else rule_matches(parsed, inputs)
        )
        if matches:
            candidates.append(
                (parsed.priority, version_id, identity, version, layer, parsed)
            )
    candidates.sort(key=lambda item: (item[0], item[1]))
    sections = [
        ContextSection(
            id=f"rule-version:{version['id']}",
            layer=layer,
            title=f"Rule · {parsed.name}",
            content=parsed.content,
            source_id=str(version["id"]),
            source_version=_version_label(version),
        )
        for _, _, _identity, version, layer, parsed in candidates
    ]
    return sections, tuple(str(version["id"]) for *_, version, _layer, _parsed in candidates)


def _skill_sections(
    repository: CustomizationContextRepository,
    workspace_id: str,
    bindings: dict[str, Any],
    active_skill_version_ids: frozenset[str],
    *,
    frozen_bindings: bool = False,
) -> tuple[list[ContextSection], tuple[str, ...]]:
    catalog: list[str] = []
    sections: list[ContextSection] = []
    active: list[str] = []
    ordered_bindings = sorted(
        (item for item in bindings.get("skills") or [] if isinstance(item, dict)),
        key=lambda item: (int(item.get("position") or 0), str(item.get("skill_version_id") or "")),
    )
    for binding in ordered_bindings:
        version_id = str(binding.get("skill_version_id") or "")
        version = repository.get_skill_version(workspace_id, version_id)
        if version is None:
            continue
        definition = _definition(version, "Skill")
        if frozen_bindings:
            identity = {"enabled": True, "slug": definition.get("name")}
        else:
            identity = repository.get_skill(
                workspace_id, str(version.get("skill_id") or "")
            )
            if identity is None or identity.get("enabled") is not True:
                continue
        name = str(definition.get("name") or identity.get("slug") or version_id)
        description = str(definition.get("description") or "").strip()
        mode = str(binding.get("mode") or "auto")
        explicitly_active = version_id in active_skill_version_ids
        user_invocable = bool(definition.get("user_invocable", True))
        disable_model_invocation = bool(definition.get("disable_model_invocation", False))

        if mode == "auto" and not disable_model_invocation:
            catalog.append(
                f"- `{name}` ({version_id}): {description} "
                "Load its instructions with `skill.load` only when relevant."
            )
        should_load = mode == "always" or (
            explicitly_active and user_invocable and mode in {"auto", "manual"}
        )
        if should_load:
            active.append(version_id)
            sections.append(
                ContextSection(
                    id=f"skill-version:{version_id}",
                    layer=ContextLayer.ACTIVE_SKILLS,
                    title=f"Active Skill · {name}",
                    content=str(definition.get("instructions") or ""),
                    source_id=version_id,
                    source_version=_version_label(version),
                )
            )

    if catalog:
        sections.insert(
            0,
            ContextSection(
                id="agent-skill-catalog",
                layer=ContextLayer.ACTIVE_SKILLS,
                title="Available Skills",
                content=(
                    "These Skills are descriptions, not permissions. Skill-declared tools never "
                    "expand the Agent tool allow-list.\n\n" + "\n".join(catalog)
                ),
                source_id="agent-version-skill-bindings",
                source_version=str(bindings.get("agent_version_id") or "unknown"),
            ),
        )
    return sections, tuple(active)


def resolve_customization_context(
    repository: CustomizationContextRepository,
    *,
    workspace_id: str,
    thread: dict[str, Any],
    run: dict[str, Any],
    definition: AgentDefinition,
    prompt: str,
    attachment_names: tuple[str, ...] = (),
    include_workspace_preferences: bool = True,
    run_snapshot: dict[str, Any] | None = None,
) -> ResolvedCustomization:
    """Resolve only authorized resources and return deterministic context sections."""
    thread_id = str(thread["id"])
    agent_version_id = str(run["agent_version_id"])
    if run_snapshot is None:
        configuration = repository.get_thread_configuration(workspace_id, thread_id)
        bindings = repository.get_agent_version_customization_bindings(
            workspace_id, agent_version_id
        )
    else:
        configuration = {
            "revision": int(run_snapshot.get("thread_configuration_revision") or 0),
            "active_skill_version_ids": list(
                run_snapshot.get("active_skill_version_ids") or []
            ),
            "manual_rule_version_ids": list(
                run_snapshot.get("manual_rule_version_ids") or []
            ),
        }
        bindings = {
            "agent_version_id": agent_version_id,
            "skills": list(run_snapshot.get("agent_skill_bindings") or []),
            "rules": [
                {"rule_version_id": item}
                for item in run_snapshot.get("agent_rule_version_ids") or []
            ],
        }
    manual_rules = frozenset(
        str(item) for item in configuration.get("manual_rule_version_ids") or []
    )
    inputs = RuleResolutionInput(
        prompt=prompt,
        thread_context=thread.get("context") or {},
        attachment_names=attachment_names,
        manually_selected=manual_rules,
    )

    sections: list[ContextSection] = []
    preferences_revision: int | None = None
    if include_workspace_preferences:
        preferences = (
            run_snapshot.get("workspace_preferences") or {}
            if run_snapshot is not None
            else repository.get_workspace_preferences(workspace_id)
        )
        content = str(preferences.get("content") or "").strip()
        if content:
            preferences_revision = int(preferences.get("revision") or 0)
            sections.append(
                ContextSection(
                    id=f"workspace-preferences:{workspace_id}",
                    layer=ContextLayer.USER_PREFERENCES,
                    title="Workspace preferences",
                    content=content,
                    source_id=workspace_id,
                    source_version=str(preferences_revision),
                )
            )

    if run_snapshot is None:
        workspace_rules = [
            (*record, ContextLayer.WORKSPACE_RULES)
            for record in _current_rule_versions(
                repository, workspace_id, scope="workspace"
            )
        ]
        bound_rules = [
            (*record, ContextLayer.AGENT_INSTRUCTIONS)
            for record in _bound_rule_versions(repository, workspace_id, bindings)
        ]
        thread_rules = [
            (*record, ContextLayer.THREAD_RULES)
            for record in _current_rule_versions(
                repository, workspace_id, scope="thread", thread_id=thread_id
            )
        ]
    else:
        def frozen_rules(
            ids: list[str], layer: ContextLayer
        ) -> list[tuple[dict[str, Any], dict[str, Any], ContextLayer]]:
            records: list[tuple[dict[str, Any], dict[str, Any], ContextLayer]] = []
            for version_id in ids:
                version = repository.get_rule_version(workspace_id, str(version_id))
                if version is not None:
                    records.append(({}, version, layer))
            return records

        workspace_rules = frozen_rules(
            list(run_snapshot.get("workspace_rule_version_ids") or []),
            ContextLayer.WORKSPACE_RULES,
        )
        bound_rules = frozen_rules(
            list(run_snapshot.get("agent_rule_version_ids") or []),
            ContextLayer.AGENT_INSTRUCTIONS,
        )
        thread_rules = frozen_rules(
            list(run_snapshot.get("thread_rule_version_ids") or []),
            ContextLayer.THREAD_RULES,
        )
    rule_sections, applied_rules = _rule_sections(
        [*workspace_rules, *bound_rules, *thread_rules], inputs
    )
    sections.extend(rule_sections)

    active_skill_ids = frozenset(
        str(item) for item in configuration.get("active_skill_version_ids") or []
    )
    skill_sections, loaded_skills = _skill_sections(
        repository,
        workspace_id,
        bindings,
        active_skill_ids,
        frozen_bindings=run_snapshot is not None,
    )
    sections.extend(skill_sections)
    return ResolvedCustomization(
        sections=tuple(sections),
        thread_configuration_revision=int(configuration.get("revision") or 0),
        workspace_preferences_revision=preferences_revision,
        active_skill_version_ids=loaded_skills,
        applied_rule_version_ids=applied_rules,
    )
