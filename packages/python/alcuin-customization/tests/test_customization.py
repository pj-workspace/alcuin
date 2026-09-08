from __future__ import annotations

import json
import zipfile
from io import BytesIO

import pytest

from alcuin_customization import (
    AGENT_PLUGIN_SCHEMA_V1,
    PluginArchiveError,
    RuleActivation,
    RuleResolutionInput,
    SkillParseError,
    inspect_plugin_archive,
    parse_rule_markdown,
    parse_skill_bundle,
    parse_skill_markdown,
    render_skill_catalog,
    render_skill_instructions,
    rule_matches,
)


def archive(files: dict[str, str | bytes]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as output:
        for path, content in files.items():
            output.writestr(path, content)
    return buffer.getvalue()


def test_skill_parser_enforces_identity_and_progressive_resources() -> None:
    skill = parse_skill_bundle(
        {
            "skills/report/SKILL.md": b"---\nname: report\ndescription: Prepare a cited report.\nmetadata:\n  alcuin:\n    required_tools: [web.search]\n---\n\nUse evidence.",
            "skills/report/references/FORMAT.md": b"# Format\nUse a decision table.",
            "skills/report/scripts/run.py": b"print('must never execute during import')",
            "skills/other/SKILL.md": b"ignored",
        },
        "skills/report/SKILL.md",
    )

    assert skill.name == "report"
    assert skill.required_tools == ("web.search",)
    assert [item.path for item in skill.resources] == ["references/FORMAT.md", "scripts/run.py"]
    assert skill.resources[0].content is not None
    assert skill.resources[1].content is None
    assert "references/FORMAT.md" in render_skill_instructions(skill)
    assert "Use evidence" not in render_skill_catalog((skill,))


def test_skill_parser_rejects_name_mismatch_and_non_boolean_policy() -> None:
    with pytest.raises(SkillParseError, match="parent directory"):
        parse_skill_bundle(
            {"skills/review/SKILL.md": b"---\nname: deploy\ndescription: Deploy safely.\n---\n\nSteps."},
            "skills/review/SKILL.md",
        )
    with pytest.raises(SkillParseError, match="boolean"):
        parse_skill_markdown(
            "---\nname: deploy\ndescription: Deploy safely.\ndisable-model-invocation: yes\n---\n\nSteps."
        )


def test_cursor_rules_map_without_silently_enabling_agent_requested() -> None:
    always = parse_rule_markdown(
        "---\nalwaysApply: true\n---\n\nAlways cite evidence.",
        source_path="rules/evidence.mdc",
    )
    requested = parse_rule_markdown(
        "---\ndescription: Use for deployments.\n---\n\nRequire a rollback plan.",
        source_path="rules/deploy.mdc",
    )
    scoped = parse_rule_markdown(
        "---\nglobs: ['*.pdf']\n---\n\nExtract citations.",
        source_path="rules/pdf.mdc",
    )

    assert always.activation == RuleActivation.ALWAYS
    assert requested.activation == RuleActivation.MANUAL
    assert requested.warnings
    assert scoped.activation == RuleActivation.CONDITIONAL
    assert rule_matches(always, RuleResolutionInput(prompt="hello"))
    assert not rule_matches(requested, RuleResolutionInput(prompt="deploy"))
    assert rule_matches(
        requested,
        RuleResolutionInput(prompt="deploy", manually_selected=frozenset({"deploy"})),
    )
    assert rule_matches(scoped, RuleResolutionInput(prompt="", attachment_names=("risk.pdf",)))


def test_agent_plugin_inspection_is_portable_and_never_executes_scripts() -> None:
    payload = archive(
        {
            "bundle/plugin.json": json.dumps(
                {
                    "$schema": AGENT_PLUGIN_SCHEMA_V1,
                    "name": "risk-review",
                    "version": "1.0.0",
                }
            ),
            "bundle/skills/risk/SKILL.md": "---\nname: risk\ndescription: Score operational risk.\n---\n\nScore with evidence.",
            "bundle/skills/risk/scripts/score.py": "raise RuntimeError('not executed')",
            "bundle/mcp.json": json.dumps(
                {
                    "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
                    "mcpServers": {
                        "search": {
                            "type": "streamable-http",
                            "url": "https://example.com/mcp",
                            "headers": {"Authorization": "${API_TOKEN}"},
                        }
                    },
                }
            ),
        }
    )

    inspected = inspect_plugin_archive(payload)

    assert inspected.format == "agent-plugin-1.0"
    assert [skill.name for skill in inspected.skills] == ["risk"]
    assert inspected.skills[0].resources[0].kind == "script"
    assert inspected.skills[0].resources[0].content is None
    assert inspected.credential_variables == ("API_TOKEN",)
    assert inspected.mcp_servers[0]["transport"] == "streamable_http"


def test_agent_plugin_isolates_invalid_components_and_ignores_nested_skills() -> None:
    payload = archive(
        {
            "plugin.json": json.dumps({"$schema": AGENT_PLUGIN_SCHEMA_V1, "name": "isolated"}),
            "skills/good/SKILL.md": "---\nname: good\ndescription: A valid skill.\n---\n\nProceed safely.",
            "skills/bad/SKILL.md": "not frontmatter",
            "skills/group/nested/SKILL.md": "---\nname: nested\ndescription: Not portable here.\n---\n\nIgnore.",
            "mcp.json": "not json",
        }
    )

    inspected = inspect_plugin_archive(payload)

    assert [skill.name for skill in inspected.skills] == ["good"]
    assert inspected.mcp_servers == ()
    assert any("bad" in warning for warning in inspected.warnings)
    assert not any(skill.name == "nested" for skill in inspected.skills)


def test_cursor_plugin_maps_rules_skills_and_disables_executable_components() -> None:
    payload = archive(
        {
            ".cursor-plugin/plugin.json": json.dumps(
                {
                    "name": "cursor-portable",
                    "version": "2.0.0",
                    "variables": {
                        "type": "object",
                        "properties": {"API_TOKEN": {"type": "string"}},
                    },
                    "hooks": "hooks/hooks.json",
                }
            ),
            "skills/review/SKILL.md": "---\nname: review\ndescription: Review a change.\n---\n\nInspect evidence.",
            "rules/careful.mdc": "---\nalwaysApply: true\n---\n\nDo not invent evidence.",
            "hooks/hooks.json": json.dumps({"hooks": {"sessionStart": [{"command": "./run.sh"}]}}),
        }
    )

    inspected = inspect_plugin_archive(payload)

    assert inspected.format == "cursor-plugin"
    assert [skill.name for skill in inspected.skills] == ["review"]
    assert [rule.name for rule in inspected.rules] == ["careful"]
    assert inspected.credential_variables == ("API_TOKEN",)
    assert {item["component"] for item in inspected.disabled_components} == {"hooks"}
    assert any(permission["id"] == "process:execute" for permission in inspected.permissions)


def test_plugin_inspection_never_echoes_secret_bearing_manifest_configuration() -> None:
    credential_sentinel = "sk-" + "A" * 24
    payload = archive(
        {
            ".cursor-plugin/plugin.json": json.dumps(
                {
                    "name": "safe-preview",
                    "version": "1.0.0",
                    "description": credential_sentinel,
                    "variables": {
                        "type": "object",
                        "properties": {"API_TOKEN": {"type": "string"}},
                    },
                    "mcpServers": "mcp.json",
                    "author": {"name": "Maintainer", "token": "literal-secret"},
                    "repository": {
                        "type": "git",
                        "url": "https://example.com/safe.git",
                        "authorization": "literal-secret",
                    },
                    "hooks": {"sessionStart": [{"command": "./run.sh --token secret"}]},
                }
            ),
            "mcp.json": json.dumps(
                {
                    "mcpServers": {
                        "private": {
                            "type": "streamable-http",
                            "url": "https://user:password@example.com/mcp?token=literal-secret",
                            "headers": {"Authorization": "literal-secret"},
                        }
                    }
                }
            ),
        }
    )

    public = inspect_plugin_archive(payload).public_dict()

    serialized = json.dumps(public["manifest"])
    assert public["manifest"] == {
        "name": "safe-preview",
        "version": "1.0.0",
        "description": "<redacted>",
        "author": {"name": "Maintainer"},
        "repository": {"type": "git", "url": "https://example.com/safe.git"},
    }
    assert "literal-secret" not in serialized
    assert "run.sh" not in serialized
    assert public["mcp_servers"][0]["url"] == "https://example.com/mcp"
    assert credential_sentinel not in json.dumps(public)
    assert public["description"] == "<redacted>"


def test_plugin_inspection_does_not_reflect_arbitrary_skill_metadata() -> None:
    payload = archive(
        {
            "plugin.json": json.dumps(
                {"$schema": AGENT_PLUGIN_SCHEMA_V1, "name": "metadata-preview"}
            ),
            "skills/review/SKILL.md": (
                "---\nname: review\ndescription: Review safely.\n"
                "metadata:\n  token: literal-secret\n  alcuin:\n"
                "    required_tools: [web.search]\n---\n\nReview evidence."
            ),
        }
    )

    public = inspect_plugin_archive(payload).public_dict()

    assert public["skills"][0]["metadata"] == {}
    assert public["skills"][0]["required_tools"] == ["web.search"]
    assert "literal-secret" not in json.dumps(public)


def test_archive_rejects_traversal_and_ambiguous_manifests() -> None:
    with pytest.raises(PluginArchiveError, match="unsafe path"):
        inspect_plugin_archive(archive({"../plugin.json": "{}"}))
    with pytest.raises(PluginArchiveError, match="both"):
        inspect_plugin_archive(
            archive(
                {
                    "plugin.json": json.dumps({"$schema": AGENT_PLUGIN_SCHEMA_V1, "name": "one"}),
                    ".cursor-plugin/plugin.json": json.dumps({"name": "two"}),
                }
            )
        )
