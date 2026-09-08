"""Deterministic behavior Rule parsing and activation."""

from __future__ import annotations

import fnmatch
import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

import yaml


class RuleActivation(StrEnum):
    ALWAYS = "always"
    CONDITIONAL = "conditional"
    MANUAL = "manual"


@dataclass(frozen=True)
class ParsedRule:
    name: str
    description: str
    content: str
    activation: RuleActivation
    conditions: dict[str, Any] = field(default_factory=dict)
    priority: int = 100
    source_path: str = ""
    content_digest: str = ""
    warnings: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "activation": self.activation.value,
            "conditions": self.conditions,
            "priority": self.priority,
            "source_path": self.source_path,
            "content_digest": self.content_digest,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class RuleResolutionInput:
    prompt: str
    thread_context: dict[str, Any] = field(default_factory=dict)
    attachment_names: tuple[str, ...] = ()
    manually_selected: frozenset[str] = frozenset()


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return {}, normalized.strip()
    end = normalized.find("\n---\n", 4)
    if end < 0:
        raise ValueError("rule frontmatter is not closed")
    try:
        loaded = yaml.safe_load(normalized[4:end])
    except yaml.YAMLError as exc:
        raise ValueError("rule frontmatter is invalid YAML") from exc
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise ValueError("rule frontmatter must be an object")
    return loaded, normalized[end + 5 :].strip()


def parse_rule_markdown(content: str | bytes, *, source_path: str) -> ParsedRule:
    raw = content.encode("utf-8") if isinstance(content, str) else content
    if len(raw) > 256 * 1024:
        raise ValueError("rule exceeds 256 KiB")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("rule must be UTF-8 text") from exc
    frontmatter, body = _split_frontmatter(text)
    if not body:
        raise ValueError("rule content cannot be blank")
    name = frontmatter.get("name")
    if not isinstance(name, str) or not name.strip():
        name = PurePosixPath(source_path).stem
    description = frontmatter.get("description")
    description = description.strip() if isinstance(description, str) else ""
    always_apply = frontmatter.get("alwaysApply")
    if always_apply is not None and not isinstance(always_apply, bool):
        raise ValueError("alwaysApply must be a boolean")
    globs = frontmatter.get("globs")
    if isinstance(globs, str):
        glob_patterns = tuple(item.strip() for item in globs.split(",") if item.strip())
    elif isinstance(globs, list) and all(isinstance(item, str) for item in globs):
        glob_patterns = tuple(item.strip() for item in globs if item.strip())
    elif globs is None:
        glob_patterns = ()
    else:
        raise ValueError("globs must be a string or list of strings")
    warnings: list[str] = []
    conditions: dict[str, Any] = {}
    if always_apply is True:
        activation = RuleActivation.ALWAYS
    elif glob_patterns:
        activation = RuleActivation.CONDITIONAL
        conditions["file_globs"] = list(glob_patterns)
    elif description:
        activation = RuleActivation.MANUAL
        warnings.append(
            "Cursor Agent Requested rules import as manual until a trusted rule-selection protocol is configured."
        )
    else:
        activation = RuleActivation.MANUAL
    priority_raw = frontmatter.get("priority", 100)
    if not isinstance(priority_raw, int) or not 0 <= priority_raw <= 1_000:
        raise ValueError("rule priority must be an integer from 0 to 1000")
    return ParsedRule(
        name=name.strip()[:80],
        description=description[:500],
        content=body,
        activation=activation,
        conditions=conditions,
        priority=priority_raw,
        source_path=source_path,
        content_digest=hashlib.sha256(raw).hexdigest(),
        warnings=tuple(warnings),
    )


def _context_has_path(context: dict[str, Any], dotted_path: str) -> bool:
    current: Any = context
    for segment in dotted_path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return False
        current = current[segment]
    return True


def rule_matches(rule: ParsedRule, inputs: RuleResolutionInput) -> bool:
    if rule.activation == RuleActivation.ALWAYS:
        return True
    if rule.activation == RuleActivation.MANUAL:
        return rule.name in inputs.manually_selected
    conditions = rule.conditions
    prompt_terms = conditions.get("prompt_terms") or []
    if prompt_terms and any(str(term).casefold() in inputs.prompt.casefold() for term in prompt_terms):
        return True
    context_paths = conditions.get("context_paths") or []
    if context_paths and all(_context_has_path(inputs.thread_context, str(path)) for path in context_paths):
        return True
    file_globs = conditions.get("file_globs") or []
    candidate_paths = list(inputs.attachment_names)
    host_files = inputs.thread_context.get("files")
    if isinstance(host_files, list):
        candidate_paths.extend(str(item) for item in host_files if isinstance(item, str))
    return bool(
        file_globs
        and any(fnmatch.fnmatch(path, str(pattern)) for path in candidate_paths for pattern in file_globs)
    )
