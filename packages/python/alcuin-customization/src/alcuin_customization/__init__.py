"""Public customization contracts."""

from .plugin_import import (
    AGENT_PLUGIN_SCHEMA_V1,
    PluginArchiveError,
    PluginBundleInspection,
    inspect_plugin_archive,
)
from .rules import (
    ParsedRule,
    RuleActivation,
    RuleResolutionInput,
    parse_rule_markdown,
    rule_matches,
)
from .runtime_tools import (
    SKILL_LOAD_TOOL_NAME,
    SKILL_READ_RESOURCE_TOOL_NAME,
    BoundSkillProvider,
    BoundSkillSnapshot,
    SkillBuiltinTools,
    SkillRegistry,
    SkillRuntimeContext,
    SkillRuntimeError,
    SkillToolDefinition,
)
from .skills import (
    ParsedSkill,
    SkillParseError,
    SkillResource,
    parse_skill_bundle,
    parse_skill_markdown,
    render_skill_catalog,
    render_skill_instructions,
)

__all__ = [
    "AGENT_PLUGIN_SCHEMA_V1",
    "SKILL_LOAD_TOOL_NAME",
    "SKILL_READ_RESOURCE_TOOL_NAME",
    "BoundSkillProvider",
    "BoundSkillSnapshot",
    "ParsedRule",
    "ParsedSkill",
    "PluginArchiveError",
    "PluginBundleInspection",
    "RuleActivation",
    "RuleResolutionInput",
    "SkillBuiltinTools",
    "SkillParseError",
    "SkillRegistry",
    "SkillResource",
    "SkillRuntimeContext",
    "SkillRuntimeError",
    "SkillToolDefinition",
    "inspect_plugin_archive",
    "parse_rule_markdown",
    "parse_skill_bundle",
    "parse_skill_markdown",
    "render_skill_catalog",
    "render_skill_instructions",
    "rule_matches",
]
