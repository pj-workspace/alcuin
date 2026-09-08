"""Safe, read-only inspection of portable Agent Plugin and Cursor Plugin archives."""

from __future__ import annotations

import json
import re
import stat
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .rules import ParsedRule, parse_rule_markdown
from .skills import ParsedSkill, SkillParseError, parse_skill_bundle, parse_skill_markdown


AGENT_PLUGIN_SCHEMA_V1 = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MAX_ARCHIVE_BYTES = 10 * 1024 * 1024
MAX_EXPANDED_BYTES = 24 * 1024 * 1024
MAX_ARCHIVE_FILES = 256
MAX_MANIFEST_BYTES = 256 * 1024
PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
SENSITIVE_MARKERS = ("authorization", "credential", "password", "secret", "token", "api_key", "apikey")
PUBLIC_MANIFEST_FIELDS = {
    "$schema",
    "name",
    "displayName",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
}
PUBLIC_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\b(?:ghp|github_pat|xox[baprs])-[_A-Za-z0-9-]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
)


class PluginArchiveError(ValueError):
    """A plugin archive is unsafe, ambiguous, or unsupported."""


@dataclass(frozen=True)
class PluginBundleInspection:
    format: str
    name: str
    version: str
    description: str
    manifest: dict[str, Any]
    skills: tuple[ParsedSkill, ...]
    rules: tuple[ParsedRule, ...]
    mcp_servers: tuple[dict[str, Any], ...]
    credential_variables: tuple[str, ...]
    disabled_components: tuple[dict[str, Any], ...]
    permissions: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]

    def public_dict(self) -> dict[str, Any]:
        return _redact_public_value({
            "format": self.format,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "manifest": self.manifest,
            "skills": [skill.public_dict() for skill in self.skills],
            "rules": [rule.public_dict() for rule in self.rules],
            "mcp_servers": list(self.mcp_servers),
            "credential_variables": list(self.credential_variables),
            "disabled_components": list(self.disabled_components),
            "permissions": list(self.permissions),
            "warnings": list(self.warnings),
            "install_state": "ready_for_disabled_install",
        })


def _safe_public_string(value: str, *, max_length: int = 2_000) -> str:
    value = "".join(
        character
        for character in value
        if character in {"\n", "\t"} or (ord(character) >= 32 and ord(character) != 127)
    )
    if any(pattern.search(value) for pattern in PUBLIC_SECRET_PATTERNS):
        return "<redacted>"
    return value[:max_length]


def _redact_public_value(value: Any) -> Any:
    """Fail-safe final projection for high-confidence credentials in display text."""
    if isinstance(value, dict):
        return {str(key)[:120]: _redact_public_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_public_value(item) for item in value]
    if isinstance(value, str):
        return _safe_public_string(value)
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return str(type(value).__name__)


def _public_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Project only typed descriptive metadata; executable config never reaches the client."""
    public: dict[str, Any] = {}
    for key in PUBLIC_MANIFEST_FIELDS:
        value = manifest.get(key)
        if isinstance(value, str):
            public[key] = _safe_public_string(value)
        elif key == "keywords" and isinstance(value, list):
            public[key] = [
                _safe_public_string(item, max_length=80)
                for item in value[:32]
                if isinstance(item, str)
            ]
        elif key == "author" and isinstance(value, dict):
            author = {
                field: _safe_public_string(item, max_length=500)
                for field in ("name", "url")
                if isinstance((item := value.get(field)), str)
            }
            if author:
                public[key] = author
        elif key == "repository" and isinstance(value, dict):
            repository = {
                field: _safe_public_string(item, max_length=500)
                for field in ("type", "url")
                if isinstance((item := value.get(field)), str)
            }
            if repository:
                public[key] = repository
    return public


def _safe_command_preview(command: str) -> str:
    leaf = command.strip().replace("\\", "/").rsplit("/", 1)[-1]
    if not leaf or len(leaf) > 120 or any(character.isspace() for character in leaf):
        return "<configured executable>"
    return _safe_public_string(leaf, max_length=120)


def _safe_url_preview(url: str) -> str:
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return "<configured remote endpoint>"
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))[:1_000]
    except ValueError:
        return "<configured remote endpoint>"


def _safe_files(payload: bytes) -> dict[str, bytes]:
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise PluginArchiveError("plugin archive exceeds 10 MiB")
    try:
        archive = zipfile.ZipFile(BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise PluginArchiveError("plugin archive is not a valid ZIP file") from exc
    try:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if len(infos) > MAX_ARCHIVE_FILES:
            raise PluginArchiveError("plugin archive contains more than 256 files")
        if sum(info.file_size for info in infos) > MAX_EXPANDED_BYTES:
            raise PluginArchiveError("expanded plugin archive exceeds 24 MiB")
        files: dict[str, bytes] = {}
        for info in infos:
            path = PurePosixPath(info.filename.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise PluginArchiveError("plugin archive contains an unsafe path")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise PluginArchiveError("plugin archives cannot contain symbolic links")
            if info.flag_bits & 0x1:
                raise PluginArchiveError("encrypted plugin archives are not supported")
            normalized = path.as_posix()
            if normalized in files:
                raise PluginArchiveError("plugin archive contains duplicate paths")
            files[normalized] = archive.read(info)
    except PluginArchiveError:
        raise
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as exc:
        raise PluginArchiveError("plugin archive could not be read safely") from exc
    finally:
        archive.close()
    if not files:
        raise PluginArchiveError("plugin archive is empty")
    first_segments = {PurePosixPath(path).parts[0] for path in files}
    if len(first_segments) == 1:
        wrapper = next(iter(first_segments))
        if not any(path in files for path in ("plugin.json", ".cursor-plugin/plugin.json")):
            prefix = f"{wrapper}/"
            files = {path.removeprefix(prefix): content for path, content in files.items() if path.startswith(prefix)}
    return files


def _json_file(files: dict[str, bytes], path: str) -> dict[str, Any]:
    raw = files.get(path)
    if raw is None or len(raw) > MAX_MANIFEST_BYTES:
        raise PluginArchiveError(f"{path} is missing or exceeds 256 KiB")
    try:
        loaded = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PluginArchiveError(f"{path} is missing or invalid JSON") from exc
    if not isinstance(loaded, dict):
        raise PluginArchiveError(f"{path} must contain an object")
    return loaded


def _skill_paths(
    files: dict[str, bytes],
    roots: tuple[str, ...],
    *,
    recursive: bool = True,
) -> list[str]:
    paths: list[str] = []
    for root in roots:
        normalized_root = root.strip().removeprefix("./").rstrip("/")
        for path in files:
            candidate = PurePosixPath(path)
            if candidate.name != "SKILL.md":
                continue
            if normalized_root and not path.startswith(f"{normalized_root}/"):
                continue
            if not recursive:
                relative = candidate.relative_to(PurePosixPath(normalized_root))
                if len(relative.parts) != 2:
                    continue
            paths.append(path)
    return sorted(set(paths))


def _parse_skills(
    files: dict[str, bytes],
    paths: list[str],
) -> tuple[tuple[ParsedSkill, ...], list[str]]:
    skills: list[ParsedSkill] = []
    warnings: list[str] = []
    for path in paths:
        try:
            skill = (
                parse_skill_markdown(files[path], source_path=path)
                if path == "SKILL.md"
                else parse_skill_bundle(files, path)
            )
        except SkillParseError as exc:
            warnings.append(f"Skipped invalid Skill {path}: {exc}")
            continue
        skills.append(skill)
    return tuple(skills), warnings


def _component_paths(value: Any, default: str) -> tuple[str, ...]:
    if value is None:
        return (default,)
    raw = [value] if isinstance(value, str) else value
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise PluginArchiveError("plugin component paths must be strings")
    cleaned: list[str] = []
    for item in raw:
        path = PurePosixPath(item.removeprefix("./"))
        if path.is_absolute() or ".." in path.parts:
            raise PluginArchiveError("plugin component path escapes the plugin root")
        cleaned.append(path.as_posix().rstrip("/"))
    return tuple(cleaned)


def _variables(manifest: dict[str, Any]) -> tuple[str, ...]:
    variables = manifest.get("variables")
    if not isinstance(variables, dict):
        return ()
    properties = variables.get("properties")
    if not isinstance(properties, dict):
        return ()
    return tuple(sorted(key for key in properties if isinstance(key, str)))


def _mcp_servers(files: dict[str, bytes], manifest: dict[str, Any]) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...], list[str]]:
    source = manifest.get("mcpServers")
    document: dict[str, Any] | None = None
    if isinstance(source, str):
        path = PurePosixPath(source.removeprefix("./")).as_posix()
        document = _json_file(files, path)
    elif isinstance(source, dict):
        document = source
    elif source is None and "mcp.json" in files:
        document = _json_file(files, "mcp.json")
    if not document:
        return (), (), []
    servers = document.get("mcpServers")
    if not isinstance(servers, dict):
        raise PluginArchiveError("mcpServers must be an object")
    previews: list[dict[str, Any]] = []
    credential_variables: set[str] = set()
    warnings: list[str] = []
    for name, raw in sorted(servers.items()):
        if not isinstance(name, str) or not isinstance(raw, dict):
            raise PluginArchiveError("each MCP server must be a named object")
        declared_type = raw.get("type")
        if declared_type is None:
            declared_type = "stdio" if raw.get("command") else "streamable_http"
        normalized_type = {
            "http": "streamable_http",
            "streamable-http": "streamable_http",
            "streamable_http": "streamable_http",
            "stdio": "stdio",
            "sse": "sse",
        }.get(str(declared_type))
        if normalized_type is None:
            warnings.append(f"MCP server {name} uses an unsupported transport and was skipped.")
            continue
        command = raw.get("command") if normalized_type == "stdio" else None
        url = raw.get("url") if normalized_type != "stdio" else None
        if normalized_type == "stdio" and not isinstance(command, str):
            warnings.append(f"Skipped invalid stdio MCP server {name}: command is required.")
            continue
        if normalized_type != "stdio" and not isinstance(url, str):
            warnings.append(f"Skipped invalid remote MCP server {name}: url is required.")
            continue
        serialized = json.dumps(raw, ensure_ascii=False)
        credential_variables.update(PLACEHOLDER.findall(serialized))
        literal_secret_fields = [
            key
            for container in (raw.get("env"), raw.get("headers"))
            if isinstance(container, dict)
            for key, value in container.items()
            if any(marker in str(key).lower() for marker in SENSITIVE_MARKERS)
            and isinstance(value, str)
            and not PLACEHOLDER.fullmatch(value)
        ]
        if literal_secret_fields:
            warnings.append(
                f"MCP server {name} contains literal secret-shaped values; values are never imported."
            )
        previews.append(
            {
                "name": name,
                "transport": normalized_type,
                "command": _safe_command_preview(command) if command else None,
                "url": _safe_url_preview(url) if url else None,
                "executable": normalized_type == "stdio",
                "credential_variables": sorted(set(PLACEHOLDER.findall(serialized))),
            }
        )
    return tuple(previews), tuple(sorted(credential_variables)), warnings


def _rule_files(
    files: dict[str, bytes], roots: tuple[str, ...]
) -> tuple[tuple[ParsedRule, ...], list[str]]:
    suffixes = {".md", ".mdc", ".markdown"}
    results: list[ParsedRule] = []
    warnings: list[str] = []
    for root in roots:
        normalized = root.strip().removeprefix("./").rstrip("/")
        for path, payload in sorted(files.items()):
            if normalized and not path.startswith(f"{normalized}/"):
                continue
            if PurePosixPath(path).suffix.lower() not in suffixes:
                continue
            try:
                results.append(parse_rule_markdown(payload, source_path=path))
            except ValueError as exc:
                warnings.append(f"Skipped invalid Rule {path}: {exc}")
    return tuple(results), warnings


def _inspect_agent(files: dict[str, bytes], manifest: dict[str, Any]) -> PluginBundleInspection:
    schema = manifest.get("$schema")
    if schema != AGENT_PLUGIN_SCHEMA_V1:
        raise PluginArchiveError("only Agent Plugins 1.0.0 is supported")
    name = manifest.get("name")
    if (
        not isinstance(name, str)
        or not re.fullmatch(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$", name)
    ):
        raise PluginArchiveError("Agent Plugin name is required")
    allowed = {"$schema", "name", "version", "description", "author", "homepage", "repository", "license", "keywords", "extensions"}
    warnings = [f"Ignored unknown Agent Plugin field: {field}" for field in sorted(set(manifest) - allowed)]
    skills, skill_warnings = _parse_skills(
        files,
        _skill_paths(files, ("skills",), recursive=False),
    )
    warnings.extend(skill_warnings)
    try:
        mcp, variables, mcp_warnings = _mcp_servers(files, manifest)
    except PluginArchiveError as exc:
        mcp, variables, mcp_warnings = (), (), [f"Skipped invalid mcp.json: {exc}"]
    warnings.extend(mcp_warnings)
    permissions: list[dict[str, Any]] = []
    if any(server["executable"] for server in mcp):
        permissions.append({"id": "process:execute", "risk": "high", "reason": "Start a local stdio MCP server", "required": True})
    if any(not server["executable"] for server in mcp):
        permissions.append({"id": "network:connect", "risk": "medium", "reason": "Connect to a remote MCP server", "required": True})
    return PluginBundleInspection(
        format="agent-plugin-1.0",
        name=name,
        version=str(manifest.get("version") or "0.0.0"),
        description=str(manifest.get("description") or "")[:500],
        manifest=_public_manifest(manifest),
        skills=skills,
        rules=(),
        mcp_servers=mcp,
        credential_variables=variables,
        disabled_components=(),
        permissions=tuple(permissions),
        warnings=tuple(warnings),
    )


def _inspect_cursor(files: dict[str, bytes], manifest: dict[str, Any]) -> PluginBundleInspection:
    name = manifest.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PluginArchiveError("Cursor Plugin name is required")
    skill_roots = _component_paths(manifest.get("skills"), "skills")
    skill_paths = _skill_paths(files, skill_roots)
    if not skill_paths and manifest.get("skills") is None and "SKILL.md" in files:
        skill_paths = ["SKILL.md"]
    skills, skill_warnings = _parse_skills(files, skill_paths)
    rule_roots = _component_paths(manifest.get("rules"), "rules")
    rules, rule_warnings = _rule_files(files, rule_roots)
    try:
        mcp, mcp_variables, mcp_warnings = _mcp_servers(files, manifest)
    except PluginArchiveError as exc:
        mcp, mcp_variables, mcp_warnings = (), (), [f"Skipped invalid MCP configuration: {exc}"]
    variables = tuple(sorted(set(_variables(manifest)) | set(mcp_variables)))
    disabled: list[dict[str, Any]] = []
    for field, reason in (
        ("hooks", "Executable hooks require a future reviewed runtime and remain disabled."),
        ("commands", "Commands will map to Starter Workflows after the Task Runtime exists."),
        ("agents", "Subagent templates require a future Multi-agent Runtime."),
    ):
        if manifest.get(field) is not None or any(path.startswith(f"{field}/") for path in files):
            disabled.append({"component": field, "reason": reason})
    permissions: list[dict[str, Any]] = []
    if any(server["executable"] for server in mcp) or any(item["component"] == "hooks" for item in disabled):
        permissions.append({"id": "process:execute", "risk": "high", "reason": "Bundle declares executable local components", "required": False})
    if any(not server["executable"] for server in mcp):
        permissions.append({"id": "network:connect", "risk": "medium", "reason": "Connect to a remote MCP server", "required": True})
    return PluginBundleInspection(
        format="cursor-plugin",
        name=name,
        version=str(manifest.get("version") or "0.0.0"),
        description=str(manifest.get("description") or "")[:500],
        manifest=_public_manifest(manifest),
        skills=skills,
        rules=rules,
        mcp_servers=mcp,
        credential_variables=variables,
        disabled_components=tuple(disabled),
        permissions=tuple(permissions),
        warnings=tuple(
            skill_warnings
            + rule_warnings
            + mcp_warnings
            + [warning for rule in rules for warning in rule.warnings]
        ),
    )


def inspect_plugin_archive(payload: bytes) -> PluginBundleInspection:
    files = _safe_files(payload)
    agent = "plugin.json" in files
    cursor = ".cursor-plugin/plugin.json" in files
    if agent and cursor:
        raise PluginArchiveError("archive contains both Agent Plugin and Cursor Plugin manifests")
    if agent:
        return _inspect_agent(files, _json_file(files, "plugin.json"))
    if cursor:
        return _inspect_cursor(files, _json_file(files, ".cursor-plugin/plugin.json"))
    raise PluginArchiveError("plugin.json or .cursor-plugin/plugin.json is required")
