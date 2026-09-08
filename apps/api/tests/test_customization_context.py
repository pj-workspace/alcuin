from __future__ import annotations

from typing import Any

from alcuin_core.contracts import AgentDefinition

from alcuin_api.customization_context import resolve_customization_context


class FakeCustomizationRepository:
    def __init__(self) -> None:
        self.preferences = {
            "workspace_id": "ws_one",
            "revision": 2,
            "content": "Answer in Chinese and keep evidence visible.",
        }
        self.configuration = {
            "thread_id": "thr_one",
            "revision": 3,
            "active_skill_version_ids": ["skv_manual"],
            "manual_rule_version_ids": ["ruv_manual"],
        }
        self.bindings = {
            "agent_version_id": "av_one",
            "skills": [
                {"skill_version_id": "skv_auto", "mode": "auto", "position": 0},
                {"skill_version_id": "skv_manual", "mode": "manual", "position": 1},
            ],
            "rules": [{"rule_version_id": "ruv_manual", "position": 0}],
        }
        self.skills = {
            "sk_auto": {"id": "sk_auto", "enabled": True},
            "sk_manual": {"id": "sk_manual", "enabled": True},
        }
        self.skill_versions = {
            "skv_auto": {
                "id": "skv_auto",
                "skill_id": "sk_auto",
                "definition_sha256": "a" * 64,
                "definition": {
                    "name": "research",
                    "description": "Research with sources.",
                    "instructions": "Gather and compare trusted evidence.",
                    "user_invocable": True,
                },
            },
            "skv_manual": {
                "id": "skv_manual",
                "skill_id": "sk_manual",
                "definition_sha256": "b" * 64,
                "definition": {
                    "name": "review",
                    "description": "Review the current result.",
                    "instructions": "Check claims against the supplied evidence.",
                    "user_invocable": True,
                },
            },
        }
        self.rules = {
            "ru_workspace": {
                "id": "ru_workspace",
                "scope": "workspace",
                "enabled": True,
                "current_version_id": "ruv_workspace",
            },
            "ru_manual": {
                "id": "ru_manual",
                "scope": "library",
                "enabled": True,
                "current_version_id": "ruv_manual",
            },
            "ru_thread": {
                "id": "ru_thread",
                "scope": "thread",
                "thread_id": "thr_one",
                "enabled": True,
                "current_version_id": "ruv_thread",
            },
            "ru_pdf": {
                "id": "ru_pdf",
                "scope": "thread",
                "thread_id": "thr_one",
                "enabled": True,
                "current_version_id": "ruv_pdf",
            },
        }
        self.rule_versions = {
            "ruv_workspace": {
                "id": "ruv_workspace",
                "rule_id": "ru_workspace",
                "definition_sha256": "c" * 64,
                "definition": {
                    "name": "Evidence",
                    "content": "Never invent evidence.",
                    "activation": "always",
                    "conditions": {},
                    "priority": 20,
                },
            },
            "ruv_manual": {
                "id": "ruv_manual",
                "rule_id": "ru_manual",
                "definition_sha256": "d" * 64,
                "definition": {
                    "name": "Careful review",
                    "content": "Run a final contradiction check.",
                    "activation": "manual",
                    "conditions": {},
                    "priority": 50,
                },
            },
            "ruv_thread": {
                "id": "ruv_thread",
                "rule_id": "ru_thread",
                "definition_sha256": "e" * 64,
                "definition": {
                    "name": "Risk",
                    "content": "Explain material risk.",
                    "activation": "conditional",
                    "conditions": {"prompt_terms": ["risk"]},
                    "priority": 30,
                },
            },
            "ruv_pdf": {
                "id": "ruv_pdf",
                "rule_id": "ru_pdf",
                "definition_sha256": "f" * 64,
                "definition": {
                    "name": "PDF review",
                    "content": "Cite the supplied report by page.",
                    "activation": "conditional",
                    "conditions": {"file_globs": ["*.pdf"]},
                    "priority": 40,
                },
            },
        }

    def get_workspace_preferences(self, _workspace_id: str) -> dict[str, Any]:
        return self.preferences

    def get_thread_configuration(self, _workspace_id: str, _thread_id: str) -> dict[str, Any]:
        return self.configuration

    def get_agent_version_customization_bindings(
        self, _workspace_id: str, _agent_version_id: str
    ) -> dict[str, Any]:
        return self.bindings

    def list_rules(
        self,
        _workspace_id: str,
        *,
        scope: str | None = None,
        thread_id: str | None = None,
        enabled_only: bool = False,
    ) -> list[dict[str, Any]]:
        return [
            record
            for record in self.rules.values()
            if (scope is None or record["scope"] == scope)
            and (thread_id is None or record.get("thread_id") == thread_id)
            and (not enabled_only or record["enabled"])
        ]

    def get_rule(self, _workspace_id: str, rule_id: str) -> dict[str, Any] | None:
        return self.rules.get(rule_id)

    def get_rule_version(
        self, _workspace_id: str, version_id: str
    ) -> dict[str, Any] | None:
        return self.rule_versions.get(version_id)

    def get_skill(self, _workspace_id: str, skill_id: str) -> dict[str, Any] | None:
        return self.skills.get(skill_id)

    def get_skill_version(
        self, _workspace_id: str, version_id: str
    ) -> dict[str, Any] | None:
        return self.skill_versions.get(version_id)


def _definition() -> AgentDefinition:
    return AgentDefinition(
        identity={"name": "General Agent"},
        instructions="Work from evidence and return a useful result.",
        skills=[
            {"skill_version_id": "skv_auto", "mode": "auto"},
            {"skill_version_id": "skv_manual", "mode": "manual"},
        ],
        rules=[{"rule_version_id": "ruv_manual"}],
    )


def test_resolves_preferences_rules_and_progressive_skills() -> None:
    resolved = resolve_customization_context(
        FakeCustomizationRepository(),
        workspace_id="ws_one",
        thread={"id": "thr_one", "context": {}},
        run={"agent_version_id": "av_one"},
        definition=_definition(),
        prompt="Please assess the risk.",
    )

    ids = [section.id for section in resolved.sections]
    assert "workspace-preferences:ws_one" in ids
    assert "rule-version:ruv_workspace" in ids
    assert "rule-version:ruv_thread" in ids
    assert "rule-version:ruv_manual" in ids
    assert "agent-skill-catalog" in ids
    assert "skill-version:skv_manual" in ids
    assert "skill-version:skv_auto" not in ids
    assert resolved.active_skill_version_ids == ("skv_manual",)
    assert resolved.applied_rule_version_ids == (
        "ruv_workspace",
        "ruv_thread",
        "ruv_manual",
    )


def test_embed_style_resolution_excludes_workspace_preferences() -> None:
    resolved = resolve_customization_context(
        FakeCustomizationRepository(),
        workspace_id="ws_one",
        thread={"id": "thr_one", "context": {}},
        run={"agent_version_id": "av_one"},
        definition=_definition(),
        prompt="Hello",
        include_workspace_preferences=False,
    )
    assert all(section.layer.value != "user_preferences" for section in resolved.sections)
    assert resolved.workspace_preferences_revision is None


def test_conditional_rule_matches_current_attachment_name() -> None:
    resolved = resolve_customization_context(
        FakeCustomizationRepository(),
        workspace_id="ws_one",
        thread={"id": "thr_one", "context": {}},
        run={"agent_version_id": "av_one"},
        definition=_definition(),
        prompt="Review the attachment.",
        attachment_names=("risk-report.pdf",),
    )
    assert "ruv_pdf" in resolved.applied_rule_version_ids


def test_run_snapshot_wins_over_later_mutable_customization_changes() -> None:
    repository = FakeCustomizationRepository()
    repository.preferences = {
        "workspace_id": "ws_one",
        "revision": 99,
        "content": "This later preference must not affect the accepted Run.",
    }
    repository.configuration = {
        "thread_id": "thr_one",
        "revision": 99,
        "active_skill_version_ids": [],
        "manual_rule_version_ids": [],
    }
    repository.bindings = {"skills": [], "rules": []}
    for identity in (*repository.skills.values(), *repository.rules.values()):
        identity["enabled"] = False

    resolved = resolve_customization_context(
        repository,
        workspace_id="ws_one",
        thread={"id": "thr_one", "context": {}},
        run={"agent_version_id": "av_one"},
        definition=_definition(),
        prompt="Please assess the risk.",
        run_snapshot={
            "thread_configuration_revision": 3,
            "active_skill_version_ids": ["skv_manual"],
            "manual_rule_version_ids": ["ruv_manual"],
            "workspace_preferences": {
                "revision": 2,
                "content": "Answer in Chinese and keep evidence visible.",
                "content_sha256": "1" * 64,
            },
            "agent_skill_bindings": [
                {"skill_version_id": "skv_auto", "mode": "auto", "position": 0},
                {
                    "skill_version_id": "skv_manual",
                    "mode": "manual",
                    "position": 1,
                },
            ],
            "agent_rule_version_ids": ["ruv_manual"],
            "workspace_rule_version_ids": ["ruv_workspace"],
            "thread_rule_version_ids": ["ruv_thread"],
        },
    )

    contents = "\n".join(section.content for section in resolved.sections)
    assert resolved.thread_configuration_revision == 3
    assert resolved.workspace_preferences_revision == 2
    assert "Answer in Chinese" in contents
    assert "later preference" not in contents
    assert resolved.active_skill_version_ids == ("skv_manual",)
    assert resolved.applied_rule_version_ids == (
        "ruv_workspace",
        "ruv_thread",
        "ruv_manual",
    )
