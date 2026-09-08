from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from urllib.parse import urlsplit

import psycopg
import pytest

from alcuin_core.contracts import AgentCreate, AgentDefinition
from alcuin_core.customization import (
    AgentRuleBinding,
    AgentSkillBinding,
    PreferenceUpdate,
    RuleCreate,
    RulePatch,
    RuleVersionCreate,
    SkillCreate,
    SkillPatch,
    SkillVersionCreate,
    ThreadConfigurationUpdate,
)
from alcuin_storage import (
    CustomizationRepository,
    RepositoryConflict,
    RuleSummary,
    SkillSummary,
)
from alcuin_storage.postgres import PostgresStore


DATABASE_URL = os.environ.get("ALCUIN_TEST_POSTGRES_URL")


def require_test_database(url: str | None) -> str | None:
    if url is None:
        return None
    if not urlsplit(url).path.removeprefix("/").endswith("_test"):
        raise RuntimeError("Refusing to reset a database whose name does not end in '_test'")
    return url


DATABASE_URL = require_test_database(DATABASE_URL)
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="ALCUIN_TEST_POSTGRES_URL is required for customization repository tests",
)


@pytest.fixture
def store() -> PostgresStore:
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute("TRUNCATE TABLE workspaces CASCADE")
    repository = PostgresStore(DATABASE_URL, pool_max_size=4)
    try:
        yield repository
    finally:
        repository.close()


def add_workspace(repository: PostgresStore, workspace_id: str) -> None:
    with repository.connection:
        repository.connection.execute(
            "INSERT INTO workspaces(id, name, created_at) VALUES (?, ?, ?)",
            (workspace_id, workspace_id, "2026-08-29T00:00:00+00:00"),
        )


def skill_payload(
    slug: str,
    *,
    enabled: bool = True,
    source_kind: str = "native",
    metadata: dict[str, object] | None = None,
    user_invocable: bool = True,
) -> SkillCreate:
    return SkillCreate.model_validate(
        {
            "slug": slug,
            "enabled": enabled,
            "source_kind": source_kind,
            "definition": {
                "name": slug,
                "description": f"{slug} verification Skill",
                "instructions": "Inspect the supplied context and return a concise result.",
                "user_invocable": user_invocable,
                "metadata": metadata or {},
                "resources": [
                    {
                        "path": "scripts/inspect.sh",
                        "kind": "script",
                        "media_type": "text/x-shellscript",
                        "size": 19,
                        "digest": "a" * 64,
                    }
                ],
            },
        }
    )


def rule_payload(
    slug: str,
    *,
    activation: str = "manual",
    scope: str = "library",
    thread_id: str | None = None,
    enabled: bool = True,
) -> RuleCreate:
    return RuleCreate.model_validate(
        {
            "slug": slug,
            "scope": scope,
            "thread_id": thread_id,
            "enabled": enabled,
            "definition": {
                "name": slug.replace("-", " ").title(),
                "content": "Keep the response grounded in supplied evidence.",
                "activation": activation,
            },
        }
    )


def agent_definition(
    name: str,
    *,
    skills: list[AgentSkillBinding] | None = None,
    rules: list[AgentRuleBinding] | None = None,
) -> AgentDefinition:
    return AgentDefinition.model_validate(
        {
            "identity": {"name": name, "description": "Customization verification"},
            "instructions": "Use only the supplied verification context.",
            "model": {"provider": "test", "model": "test-model"},
            "skills": [item.model_dump(mode="json") for item in skills or []],
            "rules": [item.model_dump(mode="json") for item in rules or []],
        }
    )


def test_skill_and_rule_crud_are_workspace_scoped_and_digest_immutable(
    store: PostgresStore,
) -> None:
    assert isinstance(store, CustomizationRepository)
    add_workspace(store, "ws_other")

    skill = store.create_skill("ws_demo", skill_payload("risk-review"))
    rule = store.create_rule("ws_demo", rule_payload("evidence-first"))

    assert skill["enabled"] is True
    assert skill["version"] == 1
    assert skill["definition"]["resources"][0]["content"] is None
    assert skill["definition_sha256"] == hashlib.sha256(
        skill_payload("risk-review").definition.model_dump_json().encode("utf-8")
    ).hexdigest()
    assert rule["version"] == 1
    assert store.get_skill("ws_other", skill["id"]) is None
    assert store.get_skill_version("ws_other", skill["current_version_id"]) is None
    assert store.get_rule("ws_other", rule["id"]) is None
    assert store.get_rule_version("ws_other", rule["current_version_id"]) is None

    updated_skill = store.create_skill_version(
        "ws_demo",
        skill["id"],
        SkillVersionCreate.model_validate(
            {
                "definition": {
                    "name": "risk-review",
                    "description": "Second immutable definition",
                    "instructions": "Review risk with the newest approved method.",
                }
            }
        ),
    )
    updated_rule = store.create_rule_version(
        "ws_demo",
        rule["id"],
        RuleVersionCreate.model_validate(
            {
                "definition": {
                    "name": "Evidence First",
                    "content": "Cite evidence and state uncertainty.",
                    "activation": "manual",
                }
            }
        ),
    )
    assert updated_skill is not None and updated_skill["version"] == 2
    assert updated_rule is not None and updated_rule["version"] == 2
    assert [item["version"] for item in store.list_skill_versions("ws_demo", skill["id"])] == [2, 1]
    assert [item["version"] for item in store.list_rule_versions("ws_demo", rule["id"])] == [2, 1]

    assert store.update_skill(
        "ws_demo", skill["id"], SkillPatch(enabled=False)
    )["enabled"] is False
    assert store.update_rule(
        "ws_demo", rule["id"], RulePatch(enabled=False)
    )["enabled"] is False
    assert store.list_skills("ws_demo", enabled_only=True) == []
    assert store.list_rules("ws_demo", enabled_only=True) == []


def test_skill_and_rule_summaries_are_bounded_scoped_and_body_safe(
    store: PostgresStore,
) -> None:
    add_workspace(store, "ws_other")
    instruction_marker = "PRIVATE_SKILL_INSTRUCTIONS_MUST_NOT_LEAK"
    metadata_marker = "PRIVATE_SKILL_METADATA_MUST_NOT_LEAK"
    resource_marker = "PRIVATE_SKILL_RESOURCE_MUST_NOT_LEAK"
    content_tail_marker = "PRIVATE_RULE_CONTENT_TAIL_MUST_NOT_LEAK"

    sensitive_skill = store.create_skill(
        "ws_demo",
        SkillCreate.model_validate(
            {
                "slug": "summary-sensitive",
                "definition": {
                    "name": "summary-sensitive",
                    "description": "Safe list description",
                    "instructions": instruction_marker,
                    "required_tools": ["knowledge.search", "web.search"],
                    "metadata": {
                        "display_name": "Summary Sensitive",
                        "private_note": metadata_marker,
                    },
                    "resources": [
                        {
                            "path": "references/private.md",
                            "kind": "reference",
                            "media_type": "text/markdown",
                            "size": len(resource_marker),
                            "digest": hashlib.sha256(
                                resource_marker.encode("utf-8")
                            ).hexdigest(),
                            "content": resource_marker,
                        }
                    ],
                },
            }
        ),
    )
    for index in range(52):
        store.create_skill("ws_demo", skill_payload(f"paged-skill-{index:02d}"))
    foreign_skill = store.create_skill(
        "ws_other", skill_payload("foreign-summary-skill")
    )

    full_rule_content = "R" * 200 + content_tail_marker
    sensitive_rule = store.create_rule(
        "ws_demo",
        RuleCreate.model_validate(
            {
                "slug": "summary-rule",
                "scope": "library",
                "definition": {
                    "name": "Summary Rule",
                    "description": "Safe Rule description",
                    "content": full_rule_content,
                    "activation": "conditional",
                    "conditions": {"prompt_terms": ["review", "evidence"]},
                },
            }
        ),
    )
    store.create_rule("ws_demo", rule_payload("second-summary-rule"))
    foreign_rule = store.create_rule(
        "ws_other", rule_payload("foreign-summary-rule")
    )

    first_page: list[SkillSummary] = store.list_skill_summaries("ws_demo")
    second_page = store.list_skill_summaries("ws_demo", offset=50)
    all_skills = store.list_skill_summaries("ws_demo", limit=100)
    assert len(first_page) == 50
    assert len(second_page) == 3
    assert len(all_skills) == 53
    assert {item["id"] for item in first_page}.isdisjoint(
        {item["id"] for item in second_page}
    )
    assert all(item["workspace_id"] == "ws_demo" for item in all_skills)
    assert foreign_skill["id"] not in {item["id"] for item in all_skills}

    summary = next(item for item in all_skills if item["id"] == sensitive_skill["id"])
    assert summary["display_name"] == "Summary Sensitive"
    assert summary["required_tools"] == ["knowledge.search", "web.search"]
    assert summary["resource_count"] == 1
    assert set(summary) == {
        "id",
        "workspace_id",
        "slug",
        "enabled",
        "current_version_id",
        "definition_sha256",
        "source_kind",
        "name",
        "display_name",
        "description",
        "disable_model_invocation",
        "user_invocable",
        "required_tools",
        "resource_count",
        "created_at",
        "updated_at",
    }
    serialized_skills = json.dumps(all_skills)
    assert instruction_marker not in serialized_skills
    assert metadata_marker not in serialized_skills
    assert resource_marker not in serialized_skills
    assert "instructions" not in serialized_skills
    assert "metadata" not in serialized_skills
    assert "resources" not in serialized_skills

    rules: list[RuleSummary] = store.list_rule_summaries("ws_demo", limit=100)
    assert len(rules) == 2
    assert all(item["workspace_id"] == "ws_demo" for item in rules)
    assert foreign_rule["id"] not in {item["id"] for item in rules}
    rule_summary = next(item for item in rules if item["id"] == sensitive_rule["id"])
    assert rule_summary["condition_count"] == 2
    assert set(rule_summary) == {
        "id",
        "workspace_id",
        "slug",
        "scope",
        "thread_id",
        "enabled",
        "current_version_id",
        "definition_sha256",
        "source_kind",
        "name",
        "description",
        "activation",
        "priority",
        "condition_count",
        "created_at",
        "updated_at",
    }
    assert "preview" not in rule_summary
    assert "content" not in rule_summary
    assert content_tail_marker not in json.dumps(rules)

    rule_page = store.list_rule_summaries("ws_demo", limit=1, offset=1)
    assert len(rule_page) == 1
    assert rule_page[0]["id"] != rules[0]["id"]

    for method in (store.list_skill_summaries, store.list_rule_summaries):
        with pytest.raises(ValueError, match="limit"):
            method("ws_demo", limit=0)
        with pytest.raises(ValueError, match="limit"):
            method("ws_demo", limit=101)
        with pytest.raises(ValueError, match="offset"):
            method("ws_demo", offset=-1)
        with pytest.raises(ValueError, match="offset"):
            method("ws_demo", offset=100_001)


def test_imported_customization_can_be_installed_disabled(store: PostgresStore) -> None:
    skill = store.create_skill(
        "ws_demo",
        skill_payload(
            "imported-skill",
            enabled=False,
            source_kind="cursor_plugin",
        ),
    )
    rule = store.create_rule(
        "ws_demo",
        rule_payload("imported-rule", enabled=False),
    )
    assert skill["enabled"] is False
    assert rule["enabled"] is False
    with pytest.raises(RepositoryConflict, match="installed disabled"):
        store.create_skill(
            "ws_demo",
            skill_payload("unsafe-import", source_kind="agent_plugin"),
        )


def test_customization_bundle_install_is_disabled_and_atomic(
    store: PostgresStore,
) -> None:
    bundle = store.install_customization_bundle(
        "ws_demo",
        skills=[
            skill_payload(
                "bundle-skill",
                enabled=False,
                source_kind="agent_plugin",
            )
        ],
        rules=[rule_payload("bundle-rule", enabled=False)],
    )
    assert [item["slug"] for item in bundle["skills"]] == ["bundle-skill"]
    assert [item["slug"] for item in bundle["rules"]] == ["bundle-rule"]
    assert bundle["skills"][0]["enabled"] is False
    assert bundle["rules"][0]["enabled"] is False

    with pytest.raises(RepositoryConflict, match="installed disabled"):
        store.install_customization_bundle(
            "ws_demo",
            skills=[skill_payload("unsafe-enabled")],
            rules=[],
        )
    with pytest.raises(RepositoryConflict, match="already exists"):
        store.install_customization_bundle(
            "ws_demo",
            skills=[
                skill_payload(
                    "bundle-skill",
                    enabled=False,
                    source_kind="agent_plugin",
                )
            ],
            rules=[rule_payload("must-roll-back", enabled=False)],
        )
    assert store.get_rule(
        "ws_demo",
        next(
            (
                item["id"]
                for item in store.list_rules("ws_demo")
                if item["slug"] == "must-roll-back"
            ),
            "rul_missing",
        ),
    ) is None


def test_agent_creation_atomically_snapshots_exact_enabled_bindings(
    store: PostgresStore,
) -> None:
    skill = store.create_skill("ws_demo", skill_payload("bound-skill"))
    rule = store.create_rule("ws_demo", rule_payload("bound-rule"))
    skill_binding = AgentSkillBinding(
        skill_version_id=skill["current_version_id"],
        mode="manual",
    )
    rule_binding = AgentRuleBinding(rule_version_id=rule["current_version_id"])
    definition = agent_definition(
        "Bound Agent",
        skills=[skill_binding],
        rules=[rule_binding],
    )

    agent = store.create_agent(
        "ws_demo",
        AgentCreate(slug="bound-agent", definition=definition),
    )
    version = store.get_agent_version("ws_demo", agent["current_version_id"])
    bindings = store.get_agent_version_customization_bindings(
        "ws_demo", agent["current_version_id"]
    )
    assert version is not None and bindings is not None
    assert version["definition_sha256"] == hashlib.sha256(
        definition.model_dump_json().encode("utf-8")
    ).hexdigest()
    assert bindings["skills"][0]["skill_version_id"] == skill["current_version_id"]
    assert bindings["skills"][0]["definition_sha256"] == skill["definition_sha256"]
    assert bindings["rules"][0]["rule_version_id"] == rule["current_version_id"]

    bound = store.get_bound_skill_version(
        "ws_demo",
        agent["current_version_id"],
        skill["current_version_id"],
    )
    assert bound is not None
    assert bound["mode"] == "manual"
    assert bound["definition_sha256"] == skill["definition_sha256"]
    assert store.get_bound_skill_version(
        "ws_demo", agent["current_version_id"], "skv_missing"
    ) is None

    second = store.create_agent_version("ws_demo", agent["id"], definition)
    assert second is not None
    assert store.get_agent_version_customization_bindings(
        "ws_demo", second["current_version_id"]
    )["skills"][0]["skill_version_id"] == skill["current_version_id"]

    idempotent = store.set_agent_version_customization_bindings(
        "ws_demo",
        agent["current_version_id"],
        skills=[skill_binding],
        rules=[rule_binding],
    )
    assert idempotent == bindings
    with pytest.raises(RepositoryConflict, match="exactly match"):
        store.set_agent_version_customization_bindings(
            "ws_demo",
            agent["current_version_id"],
            skills=[],
            rules=[],
        )


def test_foreign_or_disabled_versions_cannot_leave_partial_agent_versions(
    store: PostgresStore,
) -> None:
    add_workspace(store, "ws_other")
    foreign = store.create_skill("ws_other", skill_payload("foreign-skill"))
    disabled = store.create_skill(
        "ws_demo", skill_payload("disabled-skill", enabled=False)
    )

    for slug, version_id, message in (
        ("foreign-agent", foreign["current_version_id"], "does not belong"),
        ("disabled-agent", disabled["current_version_id"], "Disabled Skills"),
    ):
        definition = agent_definition(
            slug,
            skills=[AgentSkillBinding(skill_version_id=version_id)],
        )
        with pytest.raises(RepositoryConflict, match=message):
            store.create_agent(
                "ws_demo",
                AgentCreate(slug=slug, definition=definition),
            )

    assert {item["slug"] for item in store.list_agents("ws_demo")} == {
        "alcuin-starter"
    }

    valid_agent = store.create_agent(
        "ws_demo",
        AgentCreate(
            slug="existing-agent",
            definition=agent_definition("Existing Agent"),
        ),
    )
    original_version_id = valid_agent["current_version_id"]
    with pytest.raises(RepositoryConflict, match="does not belong"):
        store.create_agent_version(
            "ws_demo",
            valid_agent["id"],
            agent_definition(
                "Invalid Next Version",
                skills=[
                    AgentSkillBinding(
                        skill_version_id=foreign["current_version_id"]
                    )
                ],
            ),
        )
    persisted = store.get_agent("ws_demo", valid_agent["id"])
    assert persisted is not None
    assert persisted["current_version_id"] == original_version_id
    assert len(store.list_agent_versions("ws_demo", valid_agent["id"])) == 1


def test_thread_next_turn_configuration_is_exact_scoped_and_optimistic(
    store: PostgresStore,
) -> None:
    skill = store.create_skill("ws_demo", skill_payload("manual-skill"))
    extra = store.create_skill("ws_demo", skill_payload("unbound-skill"))
    model_only = store.create_skill(
        "ws_demo", skill_payload("model-only", user_invocable=False)
    )
    bound_rule = store.create_rule("ws_demo", rule_payload("manual-bound"))
    workspace_rule = store.create_rule(
        "ws_demo",
        rule_payload("manual-workspace", scope="workspace"),
    )
    always_rule = store.create_rule(
        "ws_demo", rule_payload("always-rule", activation="always")
    )
    definition = agent_definition(
        "Config Agent",
        skills=[
            AgentSkillBinding(
                skill_version_id=skill["current_version_id"], mode="manual"
            ),
            AgentSkillBinding(
                skill_version_id=model_only["current_version_id"], mode="auto"
            ),
        ],
        rules=[AgentRuleBinding(rule_version_id=bound_rule["current_version_id"])],
    )
    agent = store.create_agent(
        "ws_demo", AgentCreate(slug="config-agent", definition=definition)
    )
    thread = store.create_thread("ws_demo", agent["id"], "Config", {})

    initial = store.get_thread_configuration("ws_demo", thread["id"])
    assert initial is not None
    assert initial["revision"] == 0
    assert initial["active_skill_version_ids"] == []

    configured = store.update_thread_configuration(
        "ws_demo",
        thread["id"],
        ThreadConfigurationUpdate(
            expected_revision=0,
            active_skill_version_ids=[skill["current_version_id"]],
            manual_rule_version_ids=[
                bound_rule["current_version_id"],
                workspace_rule["current_version_id"],
            ],
        ),
    )
    assert configured is not None
    assert configured["revision"] == 1
    assert configured["agent_version_id"] == agent["current_version_id"]
    assert configured["active_skill_version_ids"] == [skill["current_version_id"]]

    with pytest.raises(RepositoryConflict, match="reload"):
        store.update_thread_configuration(
            "ws_demo",
            thread["id"],
            ThreadConfigurationUpdate(expected_revision=0),
        )
    with pytest.raises(RepositoryConflict, match="bound"):
        store.update_thread_configuration(
            "ws_demo",
            thread["id"],
            ThreadConfigurationUpdate(
                expected_revision=1,
                active_skill_version_ids=[extra["current_version_id"]],
            ),
        )
    with pytest.raises(RepositoryConflict, match="explicit user invocation"):
        store.update_thread_configuration(
            "ws_demo",
            thread["id"],
            ThreadConfigurationUpdate(
                expected_revision=1,
                active_skill_version_ids=[model_only["current_version_id"]],
            ),
        )
    with pytest.raises(RepositoryConflict, match="manually activated"):
        store.update_thread_configuration(
            "ws_demo",
            thread["id"],
            ThreadConfigurationUpdate(
                expected_revision=1,
                manual_rule_version_ids=[always_rule["current_version_id"]],
            ),
        )
    assert store.get_thread_configuration("ws_other", thread["id"]) is None


def test_workspace_operator_preferences_are_optimistic_and_secret_safe(
    store: PostgresStore,
) -> None:
    initial = store.get_workspace_preferences("ws_demo")
    assert initial is not None
    assert initial["revision"] == 0
    assert initial["content"] == ""
    assert initial["content_sha256"] == hashlib.sha256(b"").hexdigest()

    saved = store.update_workspace_preferences(
        "ws_demo",
        PreferenceUpdate(expected_revision=0, content="Prefer concise bilingual output."),
    )
    assert saved is not None
    assert saved["revision"] == 1
    assert saved["content_sha256"] == hashlib.sha256(
        saved["content"].encode("utf-8")
    ).hexdigest()

    with pytest.raises(RepositoryConflict, match="reload"):
        store.update_workspace_preferences(
            "ws_demo",
            PreferenceUpdate(expected_revision=0, content="stale"),
        )
    with pytest.raises(ValueError, match="raw credentials"):
        store.update_workspace_preferences(
            "ws_demo",
            PreferenceUpdate(expected_revision=1, content="sk-" + "A" * 24),
        )
    assert store.get_workspace_preferences("ws_missing") is None


def test_run_freezes_customization_before_later_updates(store: PostgresStore) -> None:
    skill = store.create_skill("ws_demo", skill_payload("snapshot-skill"))
    rule = store.create_rule(
        "ws_demo",
        rule_payload("snapshot-rule", scope="workspace", activation="manual"),
    )
    agent = store.create_agent(
        "ws_demo",
        AgentCreate(
            slug="snapshot-agent",
            definition=agent_definition(
                "Snapshot Agent",
                skills=[
                    AgentSkillBinding(
                        skill_version_id=skill["current_version_id"],
                        mode="manual",
                    )
                ],
            ),
        ),
    )
    thread = store.create_thread("ws_demo", agent["id"], "Snapshot", {})
    configured = store.update_thread_configuration(
        "ws_demo",
        thread["id"],
        ThreadConfigurationUpdate(
            expected_revision=0,
            active_skill_version_ids=[skill["current_version_id"]],
            manual_rule_version_ids=[rule["current_version_id"]],
        ),
    )
    assert configured is not None
    store.update_workspace_preferences(
        "ws_demo",
        PreferenceUpdate(expected_revision=0, content="Original preference."),
    )

    run = store.create_run(
        "ws_demo",
        thread["id"],
        agent["current_version_id"],
        "Review this.",
    )
    original = store.get_run_customization_snapshot("ws_demo", run["id"])
    assert original is not None

    store.update_thread_configuration(
        "ws_demo",
        thread["id"],
        ThreadConfigurationUpdate(expected_revision=1),
    )
    store.update_workspace_preferences(
        "ws_demo",
        PreferenceUpdate(expected_revision=1, content="Later preference."),
    )
    store.update_skill("ws_demo", skill["id"], SkillPatch(enabled=False))
    store.update_rule("ws_demo", rule["id"], RulePatch(enabled=False))

    frozen = store.get_run_customization_snapshot("ws_demo", run["id"])
    assert frozen == original
    snapshot = frozen["snapshot"]
    assert snapshot["thread_configuration_revision"] == 1
    assert snapshot["active_skill_version_ids"] == [skill["current_version_id"]]
    assert snapshot["manual_rule_version_ids"] == [rule["current_version_id"]]
    assert snapshot["workspace_preferences"]["content"] == "Original preference."
    assert snapshot["agent_skill_bindings"][0]["skill_version_id"] == skill[
        "current_version_id"
    ]
    assert store.get_run_customization_snapshot("ws_other", run["id"]) is None


def test_preference_revision_allows_only_one_concurrent_writer(
    store: PostgresStore,
) -> None:
    assert DATABASE_URL is not None
    second = PostgresStore(DATABASE_URL, pool_max_size=2)

    def update(index: int) -> str:
        repository = store if index == 0 else second
        try:
            repository.update_workspace_preferences(
                "ws_demo",
                PreferenceUpdate(expected_revision=0, content=f"writer {index}"),
            )
            return "saved"
        except RepositoryConflict:
            return "conflict"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(update, range(2)))
        assert sorted(outcomes) == ["conflict", "saved"]
        assert store.get_workspace_preferences("ws_demo")["revision"] == 1
    finally:
        second.close()


def test_raw_secrets_are_rejected_before_skill_storage(store: PostgresStore) -> None:
    with pytest.raises(ValueError, match="raw credentials"):
        store.create_skill(
            "ws_demo",
            skill_payload("unsafe-skill", metadata={"api_key": "literal-value"}),
        )
    assert store.list_skills("ws_demo") == []

    for index, key in enumerate(
        ("clientSecret", "accessToken", "refresh-token", "serviceAuthorization")
    ):
        with pytest.raises(ValueError, match="raw credentials"):
            store.create_skill(
                "ws_demo",
                skill_payload(
                    f"unsafe-key-{index}",
                    metadata={key: "literal-value"},
                ),
            )
    assert store.list_skills("ws_demo") == []
