import pytest
from pydantic import ValidationError

from alcuin_core.customization import (
    AgentRuleBinding,
    AgentSkillBinding,
    RuleCreate,
    RuleVersionDefinition,
    SkillResourceDefinition,
    SkillVersionDefinition,
    ThreadConfigurationUpdate,
)
from alcuin_core.contracts import AgentDefinition


def test_conditional_rules_require_deterministic_conditions() -> None:
    with pytest.raises(ValidationError, match="condition"):
        RuleVersionDefinition(name="Risk", content="Score the evidence.", activation="conditional")

    rule = RuleVersionDefinition(
        name="Risk",
        content="Score the evidence.",
        activation="conditional",
        conditions={"prompt_terms": ["risk"]},
    )
    assert rule.conditions.prompt_terms == ["risk"]


def test_thread_rules_cannot_cross_scope_shapes() -> None:
    definition = RuleVersionDefinition(name="Evidence", content="Cite sources.")
    with pytest.raises(ValidationError, match="thread_id"):
        RuleCreate(slug="evidence", scope="thread", definition=definition)
    with pytest.raises(ValidationError, match="only thread"):
        RuleCreate(slug="evidence", scope="workspace", thread_id="thr_1", definition=definition)


def test_scripts_never_store_inline_executable_content() -> None:
    with pytest.raises(ValidationError, match="executable"):
        SkillResourceDefinition(
            path="scripts/run.py",
            kind="script",
            media_type="text/x-python",
            size=8,
            digest="a" * 64,
            content="print(1)",
        )


def test_skill_tools_and_thread_configuration_are_unique() -> None:
    with pytest.raises(ValidationError, match="required_tools"):
        SkillVersionDefinition(
            name="search",
            description="Search carefully.",
            instructions="Use trusted sources.",
            required_tools=["web.search", "web.search"],
        )
    with pytest.raises(ValidationError, match="unique"):
        ThreadConfigurationUpdate(
            expected_revision=1,
            active_skill_version_ids=["skv_one", "skv_one"],
        )


def test_agent_definition_binds_exact_customization_versions_once() -> None:
    base = {
        "identity": {"name": "General Agent"},
        "instructions": "Use the exact resources bound to this immutable version.",
    }
    definition = AgentDefinition(
        **base,
        skills=[AgentSkillBinding(skill_version_id="skv_one", mode="auto")],
        rules=[AgentRuleBinding(rule_version_id="ruv_one")],
    )
    assert definition.skills[0].skill_version_id == "skv_one"
    assert definition.rules[0].rule_version_id == "ruv_one"

    with pytest.raises(ValidationError, match="Skill version bindings"):
        AgentDefinition(
            **base,
            skills=[
                AgentSkillBinding(skill_version_id="skv_one"),
                AgentSkillBinding(skill_version_id="skv_one", mode="manual"),
            ],
        )
