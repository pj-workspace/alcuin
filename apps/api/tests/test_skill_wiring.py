from __future__ import annotations

import hashlib
from typing import Any

import pytest
from alcuin_api.runtime import RuntimeRequest, _runtime_tool_names
from alcuin_api.skill_wiring import (
    RepositoryBoundSkillProvider,
    skill_tool_definitions,
)
from alcuin_api.tools import ToolContext, ToolError, ToolExecutor, ToolRegistry
from alcuin_core.contracts import AgentDefinition


def _definition() -> dict[str, Any]:
    return {
        "identity": {"name": "General Agent"},
        "instructions": "Use only exact bound capabilities and cite useful evidence.",
        "tools": ["web.search"],
        "skills": [
            {"skill_version_id": "skv_auto", "mode": "auto"},
            {"skill_version_id": "skv_manual", "mode": "manual"},
            {"skill_version_id": "skv_disabled", "mode": "auto"},
        ],
    }


def _skill_record(
    version_id: str,
    *,
    name: str,
    mode: str,
    enabled: bool = True,
    workspace_id: str = "ws_one",
    agent_version_id: str = "av_pinned",
) -> dict[str, Any]:
    resource_content = f"Reference for {name}."
    return {
        "id": version_id,
        "workspace_id": workspace_id,
        "agent_version_id": agent_version_id,
        "skill_id": f"skl_{name}",
        "mode": mode,
        "enabled": enabled,
        "definition_sha256": "a" * 64,
        "definition": {
            "name": name,
            "description": f"Use the {name} workflow.",
            "instructions": f"Follow the exact {name} instructions.",
            "user_invocable": True,
            "metadata": {
                "private_note": "must-not-leak",
                "alcuin": {
                    "required_tools": ["web.search", "admin.delete"],
                },
            },
            "resources": [
                {
                    "path": "references/GUIDE.md",
                    "kind": "reference",
                    "media_type": "text/markdown",
                    "size": len(resource_content),
                    "digest": hashlib.sha256(resource_content.encode()).hexdigest(),
                    "content": resource_content,
                },
                {
                    "path": "scripts/run.py",
                    "kind": "script",
                    "media_type": "text/x-python",
                    "size": 12,
                    "digest": "b" * 64,
                    "content": None,
                },
            ],
        },
    }


class FakeSkillRepository:
    def __init__(self, *, manual_active: bool = True) -> None:
        self.manual_active = manual_active
        self.calls: list[tuple[Any, ...]] = []
        self.records = {
            "skv_auto": _skill_record("skv_auto", name="research", mode="auto"),
            "skv_manual": _skill_record("skv_manual", name="review", mode="manual"),
            "skv_disabled": _skill_record(
                "skv_disabled",
                name="disabled",
                mode="auto",
                enabled=False,
            ),
        }

    def get_run(self, workspace_id: str, run_id: str) -> dict[str, Any] | None:
        self.calls.append(("get_run", workspace_id, run_id))
        if workspace_id != "ws_one" or run_id != "run_one":
            return None
        return {"id": run_id, "agent_version_id": "av_pinned"}

    def get_agent_version(
        self, workspace_id: str, agent_version_id: str
    ) -> dict[str, Any] | None:
        self.calls.append(("get_agent_version", workspace_id, agent_version_id))
        if workspace_id != "ws_one" or agent_version_id != "av_pinned":
            return None
        return {"id": agent_version_id, "definition": _definition()}

    def get_context_assembly(
        self, workspace_id: str, run_id: str
    ) -> dict[str, Any] | None:
        self.calls.append(("get_context_assembly", workspace_id, run_id))
        if workspace_id != "ws_one" or run_id != "run_one":
            return None
        entries = (
            [
                {
                    "id": "skill-version:skv_manual",
                    "source_id": "skv_manual",
                    "source_version": "digest-manual",
                }
            ]
            if self.manual_active
            else []
        )
        return {"entries": entries}

    def list_bound_skill_versions(
        self,
        workspace_id: str,
        agent_version_id: str,
        *,
        enabled_only: bool = True,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            (
                "list_bound_skill_versions",
                workspace_id,
                agent_version_id,
                enabled_only,
            )
        )
        if workspace_id != "ws_one" or agent_version_id != "av_pinned":
            return []
        return list(self.records.values())

    def get_bound_skill_version(
        self,
        workspace_id: str,
        agent_version_id: str,
        skill_version_id: str,
    ) -> dict[str, Any] | None:
        self.calls.append(
            (
                "get_bound_skill_version",
                workspace_id,
                agent_version_id,
                skill_version_id,
            )
        )
        if workspace_id != "ws_one" or agent_version_id != "av_pinned":
            return None
        return self.records.get(skill_version_id)


def _executor(repository: FakeSkillRepository) -> ToolExecutor:
    return ToolExecutor(ToolRegistry(skill_tool_definitions(repository)))  # type: ignore[arg-type]


def _context(*, workspace_id: str = "ws_one", run_id: str = "run_one") -> ToolContext:
    return ToolContext(
        workspace_id=workspace_id,
        run_id=run_id,
        thread_context={
            # These values are intentionally malicious; runtime scope must ignore them.
            "agent_version_id": "av_attacker",
            "active_skill_version_ids": ["skv_attacker"],
        },
    )


@pytest.mark.asyncio
async def test_handlers_use_server_derived_run_scope_and_capture_their_tool_name() -> (
    None
):
    repository = FakeSkillRepository()
    executor = _executor(repository)
    allowed = ["skill.load", "skill.read_resource"]

    loaded = await executor.execute(
        "skill.load",
        {"skill_version_id": "skv_auto"},
        allowed_names=allowed,
        context=_context(),
    )
    resource = await executor.execute(
        "skill.read_resource",
        {
            "skill_version_id": "skv_auto",
            "path": "references/GUIDE.md",
        },
        allowed_names=allowed,
        context=_context(),
    )

    assert loaded.result.data["name"] == "research"
    assert "exact research instructions" in loaded.result.data["instructions"]
    assert resource.result.data["content"] == "Reference for research."
    assert "instructions" not in loaded.result.event_data()
    assert "content" not in resource.result.event_data()
    assert "private_note" not in str(loaded.result.event_data())
    assert (
        "get_bound_skill_version",
        "ws_one",
        "av_pinned",
        "skv_auto",
    ) in repository.calls
    assert not any("av_attacker" in call for call in repository.calls)

    with pytest.raises(ToolError) as forged_scope:
        await executor.execute(
            "skill.load",
            {
                "skill_version_id": "skv_auto",
                "workspace_id": "ws_foreign",
                "agent_version_id": "av_attacker",
            },
            allowed_names=allowed,
            context=_context(),
        )
    assert forged_scope.value.code == "invalid_arguments"


@pytest.mark.asyncio
async def test_active_manual_skill_comes_only_from_persisted_run_context_trace() -> (
    None
):
    inactive_repository = FakeSkillRepository(manual_active=False)
    with pytest.raises(ToolError) as inactive:
        await _executor(inactive_repository).execute(
            "skill.load",
            {"skill_version_id": "skv_manual"},
            allowed_names=["skill.load"],
            context=_context(),
        )
    assert inactive.value.code == "skill_not_available"

    active_repository = FakeSkillRepository(manual_active=True)
    loaded = await _executor(active_repository).execute(
        "skill.load",
        {"skill_version_id": "skv_manual"},
        allowed_names=["skill.load"],
        context=_context(),
    )
    assert loaded.result.data["name"] == "review"
    assert (
        "get_context_assembly",
        "ws_one",
        "run_one",
    ) in active_repository.calls


@pytest.mark.asyncio
async def test_skill_metadata_never_expands_the_pinned_agent_tool_allow_list() -> None:
    repository = FakeSkillRepository()
    loaded = await _executor(repository).execute(
        "skill.load",
        {"skill_version_id": "skv_auto"},
        allowed_names=["skill.load"],
        context=_context(),
    )

    assert loaded.result.data["tool_access"] == {
        "already_allowed": ["web.search"],
        "unavailable": ["admin.delete"],
        "granted": [],
        "note": "Skill metadata is advisory and never changes the Agent tool allow-list.",
    }
    pinned = repository.get_agent_version("ws_one", "av_pinned")
    assert pinned is not None
    assert pinned["definition"]["tools"] == ["web.search"]


@pytest.mark.asyncio
async def test_disabled_unbound_and_cross_workspace_skill_versions_fail_closed() -> (
    None
):
    repository = FakeSkillRepository()
    executor = _executor(repository)

    for context, version_id in (
        (_context(), "skv_disabled"),
        (_context(), "skv_unbound"),
        (_context(workspace_id="ws_foreign"), "skv_auto"),
    ):
        with pytest.raises(ToolError) as denied:
            await executor.execute(
                "skill.load",
                {"skill_version_id": version_id},
                allowed_names=["skill.load"],
                context=context,
            )
        assert denied.value.code == "skill_not_available"
        assert version_id not in denied.value.message


def test_runtime_adds_only_fixed_skill_loaders_for_exact_definition_bindings() -> None:
    without_skills = AgentDefinition(
        identity={"name": "General Agent"},
        instructions="Use the explicitly configured tools only.",
        tools=["web.search"],
    )
    with_skills = AgentDefinition.model_validate(_definition())

    plain = RuntimeRequest(
        workspace_id="ws_one",
        run_id="run_one",
        prompt="hello",
        thread_context={},
        definition=without_skills,
    )
    customized = RuntimeRequest(
        workspace_id="ws_one",
        run_id="run_one",
        prompt="hello",
        thread_context={},
        definition=with_skills,
    )

    assert _runtime_tool_names(plain) == ("web.search",)
    assert _runtime_tool_names(customized) == (
        "web.search",
        "skill.load",
        "skill.read_resource",
    )
    assert "admin.delete" not in _runtime_tool_names(customized)


def test_repository_provider_rejects_foreign_shaped_records_even_if_adapter_is_faulty() -> (
    None
):
    repository = FakeSkillRepository()
    repository.records["skv_auto"] = _skill_record(
        "skv_auto",
        name="research",
        mode="auto",
        workspace_id="ws_other",
    )
    provider = RepositoryBoundSkillProvider(repository)  # type: ignore[arg-type]

    assert (
        provider.get_bound_skill_version(
            workspace_id="ws_one",
            agent_version_id="av_pinned",
            skill_version_id="skv_auto",
        )
        is None
    )
