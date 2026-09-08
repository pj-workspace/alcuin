from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest
from alcuin_customization import parse_skill_bundle
from alcuin_customization.runtime_tools import (
    SKILL_LOAD_TOOL_NAME,
    SKILL_READ_RESOURCE_TOOL_NAME,
    BoundSkillSnapshot,
    SkillBuiltinTools,
    SkillRegistry,
    SkillRuntimeContext,
    SkillRuntimeError,
)
from alcuin_customization.skills import SkillResource


def _skill(
    *,
    name: str = "risk-review",
    instructions: str = "Score risk from cited evidence.",
    disable_model_invocation: bool = False,
    user_invocable: bool = True,
):
    flags = (
        f"disable-model-invocation: {str(disable_model_invocation).lower()}\n"
        f"user-invocable: {str(user_invocable).lower()}\n"
    )
    return parse_skill_bundle(
        {
            f"skills/{name}/SKILL.md": (
                "---\n"
                f"name: {name}\n"
                "description: Review operational risk from evidence.\n"
                f"{flags}"
                "metadata:\n"
                "  private_note: must-not-reach-the-model\n"
                "  alcuin:\n"
                "    required_tools: [web.search, admin.delete]\n"
                "---\n\n"
                f"{instructions}"
            ).encode(),
            f"skills/{name}/references/RUBRIC.md": b"ABCDE-risk-rubric",
            f"skills/{name}/notes.txt": b"public-text-note",
            f"skills/{name}/assets/template.md": b"asset-must-stay-inert",
            f"skills/{name}/scripts/score.py": b"raise RuntimeError('never execute')",
        },
        f"skills/{name}/SKILL.md",
    )


class MemoryProvider:
    def __init__(self, snapshots: list[BoundSkillSnapshot]) -> None:
        self.snapshots = snapshots
        self.calls: list[tuple[str, str, str | None]] = []

    def list_bound_skill_versions(self, *, workspace_id: str, agent_version_id: str):
        self.calls.append((workspace_id, agent_version_id, None))
        return [
            snapshot
            for snapshot in self.snapshots
            if snapshot.workspace_id == workspace_id
            and snapshot.agent_version_id == agent_version_id
        ]

    def get_bound_skill_version(
        self,
        *,
        workspace_id: str,
        agent_version_id: str,
        skill_version_id: str,
    ):
        self.calls.append((workspace_id, agent_version_id, skill_version_id))
        return next(
            (
                snapshot
                for snapshot in self.snapshots
                if snapshot.workspace_id == workspace_id
                and snapshot.agent_version_id == agent_version_id
                and snapshot.skill_version_id == skill_version_id
            ),
            None,
        )


def _context(
    *,
    workspace_id: str = "ws_one",
    agent_version_id: str = "av_one",
    active: frozenset[str] = frozenset(),
) -> SkillRuntimeContext:
    return SkillRuntimeContext(
        workspace_id=workspace_id,
        agent_version_id=agent_version_id,
        active_skill_version_ids=active,
        agent_tool_allow_list=frozenset({"web.search"}),
    )


def _runtime(
    *snapshots: BoundSkillSnapshot,
) -> tuple[SkillBuiltinTools, MemoryProvider]:
    provider = MemoryProvider(list(snapshots))
    return SkillBuiltinTools(SkillRegistry(provider)), provider


def test_catalog_and_load_only_use_exact_agent_bound_versions() -> None:
    bound = BoundSkillSnapshot("ws_one", "av_one", "skv_bound", _skill())
    different_agent_version = BoundSkillSnapshot(
        "ws_one",
        "av_two",
        "skv_other_agent",
        _skill(name="other-agent"),
    )
    different_workspace = BoundSkillSnapshot(
        "ws_secret",
        "av_one",
        "skv_foreign",
        _skill(name="foreign"),
    )
    tools, provider = _runtime(bound, different_agent_version, different_workspace)

    catalog = tools.render_catalog(_context())

    assert "skv_bound" in catalog
    assert "skv_other_agent" not in catalog
    assert "skv_foreign" not in catalog
    assert provider.calls == [("ws_one", "av_one", None)]
    with pytest.raises(SkillRuntimeError) as exc_info:
        tools.load({"skill_version_id": "skv_other_agent"}, context=_context())
    assert exc_info.value.code == "skill_not_available"
    assert "skv_other_agent" not in exc_info.value.message


def test_manual_and_model_disabled_skills_require_explicit_active_version() -> None:
    manual = BoundSkillSnapshot(
        "ws_one",
        "av_one",
        "skv_manual",
        _skill(name="manual-review"),
        invocation_mode="manual",
    )
    model_disabled = BoundSkillSnapshot(
        "ws_one",
        "av_one",
        "skv_user_only",
        _skill(name="user-only", disable_model_invocation=True),
    )
    not_user_invocable = BoundSkillSnapshot(
        "ws_one",
        "av_one",
        "skv_locked",
        _skill(name="locked", disable_model_invocation=True, user_invocable=False),
    )
    tools, _ = _runtime(manual, model_disabled, not_user_invocable)

    assert tools.render_catalog(_context()) == ""
    active = _context(active=frozenset({"skv_manual", "skv_user_only", "skv_locked"}))
    catalog = tools.render_catalog(active)
    assert "skv_manual" in catalog
    assert "skv_user_only" in catalog
    assert "skv_locked" not in catalog


def test_progressive_loading_hides_body_resources_and_metadata_until_requested() -> (
    None
):
    instructions = "0123456789" * 8
    snapshot = BoundSkillSnapshot(
        "ws_one",
        "av_one",
        "skv_progressive",
        _skill(instructions=instructions),
    )
    tools, _ = _runtime(snapshot)

    catalog = tools.render_catalog(_context())
    assert instructions not in catalog
    assert "ABCDE-risk-rubric" not in catalog
    assert "must-not-reach-the-model" not in catalog

    first = tools.execute(
        SKILL_LOAD_TOOL_NAME,
        {"skill_version_id": "skv_progressive", "max_chars": 13},
        context=_context(),
    )
    assert first.data["instructions"] == instructions[:13]
    assert first.data["next_offset"] == 13
    assert first.data["eof"] is False
    assert "ABCDE-risk-rubric" not in json.dumps(first.data)
    assert "must-not-reach-the-model" not in json.dumps(first.data)
    assert {resource["path"] for resource in first.data["resources"]} == {
        "notes.txt",
        "references/RUBRIC.md",
    }

    second = tools.execute(
        SKILL_LOAD_TOOL_NAME,
        {
            "skill_version_id": "skv_progressive",
            "offset": first.data["next_offset"],
            "max_chars": 13,
        },
        context=_context(),
    )
    assert second.data["instructions"] == instructions[13:26]


def test_resource_reads_are_bounded_to_readable_snapshot_text() -> None:
    snapshot = BoundSkillSnapshot(
        "ws_one",
        "av_one",
        "skv_resources",
        _skill(),
    )
    tools, _ = _runtime(snapshot)

    first = tools.execute(
        SKILL_READ_RESOURCE_TOOL_NAME,
        {
            "skill_version_id": "skv_resources",
            "path": "references/RUBRIC.md",
            "max_chars": 6,
        },
        context=_context(),
    )
    assert first.data["content"] == "ABCDE-"
    assert first.data["next_offset"] == 6
    continuation = tools.read_resource(
        {
            "skill_version_id": "skv_resources",
            "path": "references/RUBRIC.md",
            "offset": 6,
            "max_chars": 32,
        },
        context=_context(),
    )
    assert continuation.data["content"] == "risk-rubric"
    assert continuation.data["eof"] is True

    for path in (
        "scripts/score.py",
        "assets/template.md",
        "../secret.txt",
        "/etc/passwd",
        "missing.txt",
    ):
        with pytest.raises(SkillRuntimeError) as exc_info:
            tools.read_resource(
                {"skill_version_id": "skv_resources", "path": path},
                context=_context(),
            )
        assert exc_info.value.code == "skill_resource_not_available"
        assert path not in exc_info.value.message


def test_snapshot_digest_is_checked_before_resource_content_is_returned() -> None:
    skill = _skill()
    resource = next(item for item in skill.resources if item.path == "notes.txt")
    corrupted = replace(resource, content="changed-after-import")
    skill = replace(
        skill,
        resources=tuple(
            corrupted if item.path == resource.path else item
            for item in skill.resources
        ),
    )
    tools, _ = _runtime(BoundSkillSnapshot("ws_one", "av_one", "skv_corrupted", skill))

    with pytest.raises(SkillRuntimeError) as exc_info:
        tools.read_resource(
            {"skill_version_id": "skv_corrupted", "path": "notes.txt"},
            context=_context(),
        )
    assert exc_info.value.code == "skill_provider_unavailable"
    assert "changed-after-import" not in exc_info.value.message


def test_skill_tool_metadata_never_expands_agent_allow_list() -> None:
    context = _context()
    tools, _ = _runtime(BoundSkillSnapshot("ws_one", "av_one", "skv_tools", _skill()))
    definitions = tools.definitions()
    loaded = tools.load({"skill_version_id": "skv_tools"}, context=context)

    assert [definition.name for definition in definitions] == [
        "skill.load",
        "skill.read_resource",
    ]
    assert all(definition.mutating is False for definition in definitions)
    assert context.agent_tool_allow_list == frozenset({"web.search"})
    assert loaded.data["tool_access"] == {
        "already_allowed": ["web.search"],
        "unavailable": ["admin.delete"],
        "granted": [],
        "note": "Skill metadata is advisory and never changes the Agent tool allow-list.",
    }


def test_foreign_or_faulty_provider_outputs_fail_closed_without_leaking_secrets() -> (
    None
):
    foreign = BoundSkillSnapshot(
        "ws_private_secret",
        "av_one",
        "skv_private_secret",
        _skill(name="private-skill"),
    )

    class FaultyProvider:
        def list_bound_skill_versions(
            self, *, workspace_id: str, agent_version_id: str
        ):
            return [foreign]

        def get_bound_skill_version(self, **kwargs):
            raise RuntimeError("database password is super-secret")

    tools = SkillBuiltinTools(SkillRegistry(FaultyProvider()))
    with pytest.raises(SkillRuntimeError) as catalog_error:
        tools.render_catalog(_context())
    assert catalog_error.value.code == "skill_provider_unavailable"
    assert "private" not in catalog_error.value.message

    with pytest.raises(SkillRuntimeError) as load_error:
        tools.load({"skill_version_id": "skv_secret_probe"}, context=_context())
    assert load_error.value.code == "skill_provider_unavailable"
    assert "secret" not in load_error.value.message.lower()


def test_inline_script_content_stays_inert_even_from_a_faulty_provider() -> None:
    script_content = "raise RuntimeError('this must never run or render')"
    malicious_script = SkillResource(
        path="scripts/run.py",
        kind="script",
        media_type="text/x-python",
        size=len(script_content),
        digest=hashlib.sha256(script_content.encode()).hexdigest(),
        content=script_content,
    )
    skill = replace(_skill(), resources=(malicious_script,))
    tools, _ = _runtime(BoundSkillSnapshot("ws_one", "av_one", "skv_script", skill))

    loaded = tools.load({"skill_version_id": "skv_script"}, context=_context())
    assert loaded.data["resources"] == []
    with pytest.raises(SkillRuntimeError) as exc_info:
        tools.read_resource(
            {"skill_version_id": "skv_script", "path": "scripts/run.py"},
            context=_context(),
        )
    assert exc_info.value.code == "skill_resource_not_available"
    assert script_content not in exc_info.value.message
