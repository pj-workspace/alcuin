"""Stable Rules, Skills, preferences, and next-turn configuration contracts."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictCustomizationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkillInvocationMode(StrEnum):
    AUTO = "auto"
    ALWAYS = "always"
    MANUAL = "manual"


class AgentSkillBinding(StrictCustomizationModel):
    skill_version_id: str = Field(pattern=r"^skv_[a-zA-Z0-9]+$")
    mode: SkillInvocationMode = SkillInvocationMode.AUTO


class AgentRuleBinding(StrictCustomizationModel):
    rule_version_id: str = Field(pattern=r"^ruv_[a-zA-Z0-9]+$")


class SkillResourceDefinition(StrictCustomizationModel):
    path: str = Field(min_length=1, max_length=500)
    kind: Literal["reference", "asset", "script", "resource"]
    media_type: str = Field(min_length=1, max_length=120)
    size: int = Field(ge=0, le=524_288)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    content: str | None = Field(default=None, max_length=524_288)

    @model_validator(mode="after")
    def validate_resource(self) -> "SkillResourceDefinition":
        segments = self.path.replace("\\", "/").split("/")
        if self.path.startswith(("/", "\\")) or any(
            segment in {"", ".", ".."} for segment in segments
        ):
            raise ValueError("skill resource path must stay within the Skill root")
        if self.kind == "script" and self.content is not None:
            raise ValueError("executable Skill resources cannot store inline content")
        return self


class SkillVersionDefinition(StrictCustomizationModel):
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)
    description: str = Field(min_length=1, max_length=1_024)
    instructions: str = Field(min_length=1, max_length=262_144)
    disable_model_invocation: bool = False
    user_invocable: bool = True
    required_tools: list[str] = Field(default_factory=list, max_length=32)
    paths: list[str] = Field(default_factory=list, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)
    resources: list[SkillResourceDefinition] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def validate_skill(self) -> "SkillVersionDefinition":
        if len(set(self.required_tools)) != len(self.required_tools):
            raise ValueError("required_tools must be unique")
        if len({resource.path for resource in self.resources}) != len(self.resources):
            raise ValueError("Skill resource paths must be unique")
        metadata_size = len(
            json.dumps(self.metadata, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        )
        if metadata_size > 32_768:
            raise ValueError("Skill metadata must be 32 KiB or smaller")
        return self


class SkillCreate(StrictCustomizationModel):
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)
    definition: SkillVersionDefinition
    enabled: bool = True
    source_kind: Literal["native", "agent_plugin", "cursor_plugin"] = "native"
    source_ref: str | None = Field(default=None, max_length=500)


class SkillVersionCreate(StrictCustomizationModel):
    definition: SkillVersionDefinition


class SkillPatch(StrictCustomizationModel):
    enabled: bool


class RuleActivation(StrEnum):
    ALWAYS = "always"
    CONDITIONAL = "conditional"
    MANUAL = "manual"


class RuleConditions(StrictCustomizationModel):
    prompt_terms: list[str] = Field(default_factory=list, max_length=32)
    context_paths: list[str] = Field(default_factory=list, max_length=32)
    file_globs: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validate_conditions(self) -> "RuleConditions":
        for collection in (self.prompt_terms, self.context_paths, self.file_globs):
            if any(not item.strip() or len(item) > 200 for item in collection):
                raise ValueError("Rule condition values must be non-empty and at most 200 characters")
        return self


class RuleVersionDefinition(StrictCustomizationModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    content: str = Field(min_length=1, max_length=100_000)
    activation: RuleActivation = RuleActivation.ALWAYS
    conditions: RuleConditions = Field(default_factory=RuleConditions)
    priority: int = Field(default=100, ge=0, le=1_000)

    @model_validator(mode="after")
    def validate_rule(self) -> "RuleVersionDefinition":
        has_conditions = any(
            (self.conditions.prompt_terms, self.conditions.context_paths, self.conditions.file_globs)
        )
        if self.activation == RuleActivation.CONDITIONAL and not has_conditions:
            raise ValueError("conditional Rules require at least one deterministic condition")
        if self.activation != RuleActivation.CONDITIONAL and has_conditions:
            raise ValueError("only conditional Rules may declare conditions")
        return self


class RuleCreate(StrictCustomizationModel):
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)
    scope: Literal["workspace", "thread", "library"]
    thread_id: str | None = None
    definition: RuleVersionDefinition
    enabled: bool = True
    source_kind: Literal["native", "agent_plugin", "cursor_plugin"] = "native"
    source_ref: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_scope(self) -> "RuleCreate":
        if self.scope == "thread" and not self.thread_id:
            raise ValueError("thread Rules require thread_id")
        if self.scope != "thread" and self.thread_id:
            raise ValueError("only thread Rules may declare thread_id")
        return self


class RuleVersionCreate(StrictCustomizationModel):
    definition: RuleVersionDefinition


class RulePatch(StrictCustomizationModel):
    enabled: bool


class ThreadConfigurationUpdate(StrictCustomizationModel):
    expected_revision: int = Field(ge=0)
    active_skill_version_ids: list[str] = Field(default_factory=list, max_length=16)
    manual_rule_version_ids: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validate_unique_refs(self) -> "ThreadConfigurationUpdate":
        if len(set(self.active_skill_version_ids)) != len(self.active_skill_version_ids):
            raise ValueError("active Skill versions must be unique")
        if len(set(self.manual_rule_version_ids)) != len(self.manual_rule_version_ids):
            raise ValueError("manual Rule versions must be unique")
        return self


class PreferenceUpdate(StrictCustomizationModel):
    expected_revision: int = Field(ge=0)
    content: str = Field(default="", max_length=20_000)
