"""Database-neutral SQL repository behavior used by the PostgreSQL adapter."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from alcuin_core.contracts import (
    AgentCreate,
    AgentDefinition,
    ExtensionManifest,
    KnowledgeSourceCreate,
    utc_now,
)
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

from .errors import (
    ArtifactVersionConflict,
    AttachmentBindingError,
    RepositoryConflict,
    TaskRevisionConflict,
    TaskTransitionConflict,
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


class SqlRepository:
    """Shared SQL behavior; concrete adapters own connections and migrations."""

    _SUMMARY_DEFAULT_LIMIT = 50
    _SUMMARY_MAX_LIMIT = 100
    _SUMMARY_MAX_OFFSET = 100_000

    def close(self) -> None:
        self.connection.close()

    def _one(self, query: str, params: tuple[Any, ...]) -> Mapping[str, Any] | None:
        return self.connection.execute(query, params).fetchone()

    def _all(
        self, query: str, params: tuple[Any, ...] = ()
    ) -> list[Mapping[str, Any]]:
        return list(self.connection.execute(query, params).fetchall())

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    _HIGH_CONFIDENCE_SECRET_PATTERNS = (
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
        re.compile(r"\b(?:ghp|github_pat|xox[baprs])-[_A-Za-z0-9-]{20,}\b"),
        re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    )
    _SENSITIVE_VALUE_KEYS = {
        "apikey",
        "accesstoken",
        "refreshtoken",
        "clientsecret",
        "authorization",
        "password",
        "secret",
        "token",
        "credential",
        "credentialref",
    }

    @classmethod
    def _validate_no_raw_secrets(cls, value: Any, *, key: str | None = None) -> None:
        """Reject credential-shaped values while allowing opaque Secret References."""
        if isinstance(value, Mapping):
            for nested_key, nested_value in value.items():
                cls._validate_no_raw_secrets(
                    nested_value,
                    key=str(nested_key).strip().casefold().replace("-", "_"),
                )
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                cls._validate_no_raw_secrets(item, key=key)
            return
        if not isinstance(value, str) or not value:
            return
        stripped = value.strip()
        canonical_key = re.sub(r"[^a-z0-9]", "", key or "")
        sensitive_key = canonical_key in cls._SENSITIVE_VALUE_KEYS or canonical_key.endswith(
            ("apikey", "accesstoken", "refreshtoken", "clientsecret", "password", "authorization")
        )
        if sensitive_key and not stripped.startswith("secret://"):
            raise ValueError("raw credentials cannot be persisted; use a secret:// reference")
        if any(pattern.search(stripped) for pattern in cls._HIGH_CONFIDENCE_SECRET_PATTERNS):
            raise ValueError("raw credentials cannot be persisted; use a secret:// reference")

    @classmethod
    def _validate_persisted_parts(cls, parts: list[dict[str, Any]]) -> None:
        """Persist only resource references; inline data URLs belong to the active request."""
        if not isinstance(parts, list) or not all(isinstance(part, dict) for part in parts):
            raise ValueError("message parts must be a list of objects")
        if not parts:
            raise ValueError("a persisted conversation message requires at least one part")

        def contains_inline_data(value: Any) -> bool:
            if isinstance(value, Mapping):
                return any(
                    str(key).casefold() == "data_url" or contains_inline_data(item)
                    for key, item in value.items()
                )
            if isinstance(value, (list, tuple)):
                return any(contains_inline_data(item) for item in value)
            return isinstance(value, str) and value.lstrip().casefold().startswith("data:")

        if contains_inline_data(parts):
            raise ValueError("inline data URLs cannot be persisted in conversation messages")

    @staticmethod
    def _decoded_json(value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value

    @classmethod
    def _summary_page(cls, *, limit: int, offset: int) -> tuple[int, int]:
        if isinstance(limit, bool) or not 1 <= limit <= cls._SUMMARY_MAX_LIMIT:
            raise ValueError(
                f"summary limit must be between 1 and {cls._SUMMARY_MAX_LIMIT}"
            )
        if (
            isinstance(offset, bool)
            or offset < 0
            or offset > cls._SUMMARY_MAX_OFFSET
        ):
            raise ValueError(
                f"summary offset must be between 0 and {cls._SUMMARY_MAX_OFFSET}"
            )
        return limit, offset

    @classmethod
    def _skill_summary(cls, row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["required_tools"] = cls._decoded_json(
            result.pop("required_tools_json")
        )
        result["resource_count"] = int(result["resource_count"])
        return result

    @staticmethod
    def _rule_summary(row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["condition_count"] = int(result["condition_count"])
        return result

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Store a conservative tokenizer-independent estimate for pagination and budgets."""
        if not text:
            return 0
        return max(1, (len(text.encode("utf-8")) + 2) // 3)

    @classmethod
    def _message(cls, row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["parts"] = cls._decoded_json(result.pop("parts_json"))
        return result

    @classmethod
    def _attachment(cls, row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["metadata"] = cls._decoded_json(result.pop("metadata_json", "{}"))
        result["bound"] = bool(result.pop("bound", False))
        return result

    @staticmethod
    def _artifact(row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result.pop("content_sha256", None)
        result.pop("generation_key", None)
        return result

    @classmethod
    def _compaction(cls, row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["source_message_ids"] = cls._decoded_json(
            result.pop("source_message_ids_json")
        )
        return result

    @classmethod
    def _context_assembly(cls, row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["entries"] = cls._decoded_json(result.pop("entries_json"))
        result["normalized_input"] = cls._decoded_json(
            result.pop("normalized_input_json")
        )
        return result

    @classmethod
    def _thread(cls, row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["context"] = cls._decoded_json(result.pop("context_json"))
        result["last_message_sequence"] = int(result["next_message_sequence"])
        return result

    @staticmethod
    def _run(row: Mapping[str, Any]) -> dict[str, Any]:
        return dict(row)

    @staticmethod
    def _definition_snapshot(definition: AgentDefinition) -> tuple[str, str]:
        serialized = definition.model_dump_json()
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        return serialized, digest

    @staticmethod
    def _customization_snapshot(definition: Any) -> tuple[str, str]:
        serialized = definition.model_dump_json()
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        return serialized, digest

    @staticmethod
    def _agent(
        row: Mapping[str, Any], definition: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        result = dict(row)
        if definition is not None:
            result["definition"] = definition
        return result

    @classmethod
    def _customization_identity(
        cls,
        row: Mapping[str, Any],
    ) -> dict[str, Any]:
        result = dict(row)
        definition_json = result.pop("definition_json", None)
        if definition_json is not None:
            result["definition"] = cls._decoded_json(definition_json)
        return result

    @classmethod
    def _customization_version(
        cls,
        row: Mapping[str, Any],
    ) -> dict[str, Any]:
        result = dict(row)
        result["definition"] = cls._decoded_json(result.pop("definition_json"))
        return result

    def seed_starter(self) -> None:
        """Seed only a domain-neutral workspace and Agent for local first run."""
        definition = AgentDefinition.model_validate(
            {
                "identity": {
                    "name": "Alcuin Starter",
                    "description": "A quiet, extensible Agent ready for your own context and capabilities.",
                    "icon": "spark",
                },
                "instructions": (
                    "Help the user with the context they explicitly provide. Use only capabilities "
                    "bound to this Agent version, keep answers concise, and ask before any external change."
                ),
                "model": {
                    "provider": "deepseek",
                    "model": "deepseek-v4-flash-vision-exp",
                    "credential_ref": "secret://workspace/deepseek-primary",
                },
                "runtime": {"adapter": "langgraph-react", "max_steps": 8},
                "policies": {
                    "mutating_tools": "ask",
                    "external_side_effects": "ask",
                },
                "context_policy": {
                    "accepted": ["page", "record", "selection"],
                    "max_bytes": 16_384,
                },
                "output_schema": {"type": "artifact", "format": "markdown"},
                "starter_prompts": [
                    "Summarize the context shared by this host",
                    "Turn these notes into a concise working document",
                    "What capabilities are currently available to you?",
                ],
            }
        )
        created_at = utc_now()
        definition_json, definition_sha256 = self._definition_snapshot(definition)
        with self.lock, self.connection:
            self.connection.execute(
                """INSERT INTO workspaces(id, name, created_at) VALUES (?, ?, ?)
                ON CONFLICT DO NOTHING""",
                ("ws_demo", "Alcuin Workspace", created_at),
            )
            self.connection.execute(
                """INSERT INTO agents
                (id, workspace_id, slug, name, description, status, current_version_id,
                 published_version_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING""",
                (
                    "agt_starter",
                    "ws_demo",
                    "alcuin-starter",
                    definition.identity.name,
                    definition.identity.description,
                    "published",
                    "av_starter_1",
                    "av_starter_1",
                    created_at,
                    created_at,
                ),
            )
            self.connection.execute(
                """INSERT INTO agent_versions
                (id, workspace_id, agent_id, version, definition_json, definition_sha256,
                 created_at, published_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING""",
                (
                    "av_starter_1",
                    "ws_demo",
                    "agt_starter",
                    1,
                    definition_json,
                    definition_sha256,
                    created_at,
                    created_at,
                ),
            )

    def workspace(self, workspace_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM workspaces WHERE id = ?", (workspace_id,))
        return dict(row) if row else None

    def list_agents(self, workspace_id: str) -> list[dict[str, Any]]:
        rows = self._all(
            """SELECT a.*, v.version, v.definition_json
            FROM agents a LEFT JOIN agent_versions v ON v.id = a.current_version_id
            WHERE a.workspace_id = ? ORDER BY a.created_at""",
            (workspace_id,),
        )
        return [
            self._agent(
                row,
                json.loads(row["definition_json"]) if row["definition_json"] else None,
            )
            for row in rows
        ]

    def get_agent(self, workspace_id: str, agent_id: str) -> dict[str, Any] | None:
        row = self._one(
            """SELECT a.*, v.version, v.definition_json
            FROM agents a LEFT JOIN agent_versions v ON v.id = a.current_version_id
            WHERE a.workspace_id = ? AND a.id = ?""",
            (workspace_id, agent_id),
        )
        if not row:
            return None
        return self._agent(
            row, json.loads(row["definition_json"]) if row["definition_json"] else None
        )

    def get_agent_version(
        self, workspace_id: str, version_id: str
    ) -> dict[str, Any] | None:
        row = self._one(
            "SELECT * FROM agent_versions WHERE workspace_id = ? AND id = ?",
            (workspace_id, version_id),
        )
        if not row:
            return None
        result = dict(row)
        result["definition"] = json.loads(result.pop("definition_json"))
        return result

    def list_agent_versions(
        self,
        workspace_id: str,
        agent_id: str,
    ) -> list[dict[str, Any]]:
        rows = self._all(
            """SELECT * FROM agent_versions
            WHERE workspace_id = ? AND agent_id = ? ORDER BY version DESC""",
            (workspace_id, agent_id),
        )
        versions: list[dict[str, Any]] = []
        for row in rows:
            materialized = dict(row)
            materialized["definition"] = json.loads(
                materialized.pop("definition_json")
            )
            versions.append(materialized)
        return versions

    def _validate_customization_bindings_locked(
        self,
        workspace_id: str,
        skills: list[AgentSkillBinding],
        rules: list[AgentRuleBinding],
    ) -> None:
        for binding in skills:
            row = self._one(
                """SELECT sv.id, s.enabled
                FROM skill_versions sv
                JOIN skills s
                  ON s.id = sv.skill_id AND s.workspace_id = sv.workspace_id
                WHERE sv.workspace_id = ? AND sv.id = ?""",
                (workspace_id, binding.skill_version_id),
            )
            if row is None:
                raise RepositoryConflict(
                    "Skill version does not belong to this Workspace"
                )
            if not row["enabled"]:
                raise RepositoryConflict("Disabled Skills cannot be bound to an Agent version")
        for binding in rules:
            row = self._one(
                """SELECT rv.id, r.enabled, r.scope
                FROM rule_versions rv
                JOIN rules r
                  ON r.id = rv.rule_id AND r.workspace_id = rv.workspace_id
                WHERE rv.workspace_id = ? AND rv.id = ?""",
                (workspace_id, binding.rule_version_id),
            )
            if row is None:
                raise RepositoryConflict(
                    "Rule version does not belong to this Workspace"
                )
            if not row["enabled"]:
                raise RepositoryConflict("Disabled Rules cannot be bound to an Agent version")
            if row["scope"] == "thread":
                raise RepositoryConflict(
                    "Thread-scoped Rules cannot be bound to a reusable Agent version"
                )

    def _insert_agent_version_customization_bindings_locked(
        self,
        workspace_id: str,
        agent_version_id: str,
        skills: list[AgentSkillBinding],
        rules: list[AgentRuleBinding],
        created_at: str,
    ) -> None:
        for position, binding in enumerate(skills):
            self.connection.execute(
                """INSERT INTO agent_version_skill_bindings
                (workspace_id, agent_version_id, skill_version_id, mode, position, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    workspace_id,
                    agent_version_id,
                    binding.skill_version_id,
                    binding.mode.value,
                    position,
                    created_at,
                ),
            )
        for position, binding in enumerate(rules):
            self.connection.execute(
                """INSERT INTO agent_version_rule_bindings
                (workspace_id, agent_version_id, rule_version_id, position, created_at)
                VALUES (?, ?, ?, ?, ?)""",
                (
                    workspace_id,
                    agent_version_id,
                    binding.rule_version_id,
                    position,
                    created_at,
                ),
            )

    def get_agent_version_customization_bindings(
        self,
        workspace_id: str,
        agent_version_id: str,
    ) -> dict[str, Any] | None:
        version = self._one(
            """SELECT id FROM agent_versions
            WHERE workspace_id = ? AND id = ?""",
            (workspace_id, agent_version_id),
        )
        if version is None:
            return None
        skills = self._all(
            """SELECT b.skill_version_id, b.mode, b.position,
                v.definition_sha256
            FROM agent_version_skill_bindings b
            JOIN skill_versions v
              ON v.id = b.skill_version_id AND v.workspace_id = b.workspace_id
            WHERE b.workspace_id = ? AND b.agent_version_id = ?
            ORDER BY b.position""",
            (workspace_id, agent_version_id),
        )
        rules = self._all(
            """SELECT b.rule_version_id, b.position, v.definition_sha256
            FROM agent_version_rule_bindings b
            JOIN rule_versions v
              ON v.id = b.rule_version_id AND v.workspace_id = b.workspace_id
            WHERE b.workspace_id = ? AND b.agent_version_id = ?
            ORDER BY b.position""",
            (workspace_id, agent_version_id),
        )
        return {
            "agent_version_id": agent_version_id,
            "skills": [dict(row) for row in skills],
            "rules": [dict(row) for row in rules],
        }

    def set_agent_version_customization_bindings(
        self,
        workspace_id: str,
        agent_version_id: str,
        *,
        skills: list[AgentSkillBinding],
        rules: list[AgentRuleBinding],
    ) -> dict[str, Any] | None:
        """Idempotent migration/repair seam; normal creation writes bindings atomically."""
        with self.lock, self.connection:
            version = self._one(
                """SELECT definition_json FROM agent_versions
                WHERE workspace_id = ? AND id = ? FOR UPDATE""",
                (workspace_id, agent_version_id),
            )
            if version is None:
                return None
            definition = self._decoded_json(version["definition_json"])
            expected_skills = definition.get("skills", [])
            expected_rules = definition.get("rules", [])
            supplied_skills = [item.model_dump(mode="json") for item in skills]
            supplied_rules = [item.model_dump(mode="json") for item in rules]
            if supplied_skills != expected_skills or supplied_rules != expected_rules:
                raise RepositoryConflict(
                    "Bindings must exactly match the immutable Agent definition"
                )
            self._validate_customization_bindings_locked(workspace_id, skills, rules)
            existing_skills = self._all(
                """SELECT skill_version_id, mode FROM agent_version_skill_bindings
                WHERE workspace_id = ? AND agent_version_id = ? ORDER BY position""",
                (workspace_id, agent_version_id),
            )
            existing_rules = self._all(
                """SELECT rule_version_id FROM agent_version_rule_bindings
                WHERE workspace_id = ? AND agent_version_id = ? ORDER BY position""",
                (workspace_id, agent_version_id),
            )
            normalized_skills = [
                {"skill_version_id": row["skill_version_id"], "mode": row["mode"]}
                for row in existing_skills
            ]
            normalized_rules = [
                {"rule_version_id": row["rule_version_id"]} for row in existing_rules
            ]
            if normalized_skills or normalized_rules:
                if normalized_skills != supplied_skills or normalized_rules != supplied_rules:
                    raise RepositoryConflict(
                        "Immutable Agent version bindings cannot be replaced"
                    )
            elif skills or rules:
                self._insert_agent_version_customization_bindings_locked(
                    workspace_id,
                    agent_version_id,
                    skills,
                    rules,
                    utc_now(),
                )
        return self.get_agent_version_customization_bindings(
            workspace_id,
            agent_version_id,
        )

    def create_agent(self, workspace_id: str, payload: AgentCreate) -> dict[str, Any]:
        agent_id, version_id, created_at = new_id("agt"), new_id("av"), utc_now()
        self._validate_no_raw_secrets(payload.definition.model_dump(mode="json"))
        definition_json, definition_sha256 = self._definition_snapshot(
            payload.definition
        )
        try:
            with self.lock, self.connection:
                self._validate_customization_bindings_locked(
                    workspace_id,
                    payload.definition.skills,
                    payload.definition.rules,
                )
                self.connection.execute(
                    """INSERT INTO agents
                    (id, workspace_id, slug, name, description, status, current_version_id,
                     published_version_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'draft', ?, NULL, ?, ?)""",
                    (
                        agent_id,
                        workspace_id,
                        payload.slug,
                        payload.definition.identity.name,
                        payload.definition.identity.description,
                        version_id,
                        created_at,
                        created_at,
                    ),
                )
                self.connection.execute(
                    """INSERT INTO agent_versions
                    (id, workspace_id, agent_id, version, definition_json, definition_sha256,
                     created_at, published_at)
                    VALUES (?, ?, ?, 1, ?, ?, ?, NULL)""",
                    (
                        version_id,
                        workspace_id,
                        agent_id,
                        definition_json,
                        definition_sha256,
                        created_at,
                    ),
                )
                self._insert_agent_version_customization_bindings_locked(
                    workspace_id,
                    version_id,
                    payload.definition.skills,
                    payload.definition.rules,
                    created_at,
                )
        except Exception as exc:
            if self._is_unique_violation(
                exc,
                postgres_constraint="agents_workspace_id_slug_key",
            ):
                raise RepositoryConflict(
                    "Agent slug already exists in this workspace"
                ) from exc
            raise
        return self.get_agent(workspace_id, agent_id) or {}

    def create_agent_version(
        self, workspace_id: str, agent_id: str, definition: AgentDefinition
    ) -> dict[str, Any] | None:
        version_id = new_id("av")
        created_at = utc_now()
        self._validate_no_raw_secrets(definition.model_dump(mode="json"))
        definition_json, definition_sha256 = self._definition_snapshot(definition)
        with self.lock, self.connection:
            agent = self._one(
                """SELECT id FROM agents
                WHERE workspace_id = ? AND id = ? FOR UPDATE""",
                (workspace_id, agent_id),
            )
            if agent is None:
                return None
            self._validate_customization_bindings_locked(
                workspace_id,
                definition.skills,
                definition.rules,
            )
            row = self._one(
                """SELECT COALESCE(MAX(version), 0) AS value FROM agent_versions
                WHERE workspace_id = ? AND agent_id = ?""",
                (workspace_id, agent_id),
            )
            version = int(row["value"]) + 1 if row else 1
            self.connection.execute(
                """INSERT INTO agent_versions
                (id, workspace_id, agent_id, version, definition_json, definition_sha256,
                 created_at, published_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL)""",
                (
                    version_id,
                    workspace_id,
                    agent_id,
                    version,
                    definition_json,
                    definition_sha256,
                    created_at,
                ),
            )
            self._insert_agent_version_customization_bindings_locked(
                workspace_id,
                version_id,
                definition.skills,
                definition.rules,
                created_at,
            )
            self.connection.execute(
                """UPDATE agents
                SET current_version_id = ?, name = ?, description = ?, updated_at = ?
                WHERE id = ? AND workspace_id = ?""",
                (
                    version_id,
                    definition.identity.name,
                    definition.identity.description,
                    created_at,
                    agent_id,
                    workspace_id,
                ),
            )
        return self.get_agent(workspace_id, agent_id)

    def publish_agent(self, workspace_id: str, agent_id: str) -> dict[str, Any] | None:
        """Compatibility publish that snapshots the locked current version exactly once."""
        with self.lock, self.connection:
            agent = self._one(
                """SELECT current_version_id FROM agents
                WHERE workspace_id = ? AND id = ? FOR UPDATE""",
                (workspace_id, agent_id),
            )
            if agent is None or not agent.get("current_version_id"):
                return None
            self._publish_agent_version_locked(
                workspace_id,
                agent_id,
                str(agent["current_version_id"]),
                utc_now(),
            )
        return self.get_agent(workspace_id, agent_id)

    def _publish_agent_version_locked(
        self,
        workspace_id: str,
        agent_id: str,
        version_id: str,
        published_at: str,
    ) -> None:
        version = self._one(
            """SELECT id FROM agent_versions
            WHERE workspace_id = ? AND agent_id = ? AND id = ?""",
            (workspace_id, agent_id, version_id),
        )
        if version is None:
            raise RepositoryConflict(
                "Agent version does not belong to this Agent and Workspace"
            )
        self.connection.execute(
            """UPDATE agent_versions SET published_at = COALESCE(published_at, ?)
            WHERE workspace_id = ? AND agent_id = ? AND id = ?""",
            (published_at, workspace_id, agent_id, version_id),
        )
        self.connection.execute(
            """UPDATE agents
            SET status = 'published', published_version_id = ?, updated_at = ?
            WHERE workspace_id = ? AND id = ?""",
            (version_id, published_at, workspace_id, agent_id),
        )

    def publish_agent_version(
        self,
        workspace_id: str,
        agent_id: str,
        version_id: str,
    ) -> dict[str, Any] | None:
        published_at = utc_now()
        with self.lock, self.connection:
            agent = self._one(
                """SELECT id FROM agents
                WHERE workspace_id = ? AND id = ? FOR UPDATE""",
                (workspace_id, agent_id),
            )
            if agent is None:
                return None
            self._publish_agent_version_locked(
                workspace_id,
                agent_id,
                version_id,
                published_at,
            )
        return self.get_agent(workspace_id, agent_id)

    def create_thread(
        self,
        workspace_id: str,
        agent_id: str,
        title: str,
        context: dict[str, Any],
        *,
        agent_version_id: str | None = None,
    ) -> dict[str, Any]:
        thread_id = new_id("thr")
        created_at = utc_now()
        with self.lock, self.connection:
            agent = self._one(
                """SELECT current_version_id FROM agents
                WHERE workspace_id = ? AND id = ? FOR UPDATE""",
                (workspace_id, agent_id),
            )
            if agent is None:
                raise RepositoryConflict("Agent does not exist in this Workspace")
            selected_version_id = agent_version_id or agent["current_version_id"]
            version = self._one(
                """SELECT id FROM agent_versions
                WHERE workspace_id = ? AND agent_id = ? AND id = ?""",
                (workspace_id, agent_id, selected_version_id),
            )
            if version is None:
                raise RepositoryConflict(
                    "Agent version does not belong to this Agent and Workspace"
                )
            self.connection.execute(
                """INSERT INTO threads
                (id, workspace_id, agent_id, agent_version_id, title, context_json,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    thread_id,
                    workspace_id,
                    agent_id,
                    selected_version_id,
                    title,
                    self._json(context),
                    created_at,
                    created_at,
                ),
            )
            self.connection.execute(
                """INSERT INTO thread_configurations
                (thread_id, workspace_id, revision, created_at, updated_at)
                VALUES (?, ?, 0, ?, ?)""",
                (thread_id, workspace_id, created_at, created_at),
            )
        return self.get_thread(workspace_id, thread_id) or {}

    def get_thread(self, workspace_id: str, thread_id: str) -> dict[str, Any] | None:
        row = self._one(
            """SELECT t.*,
            (SELECT c.id FROM thread_compactions c
             WHERE c.workspace_id = t.workspace_id AND c.thread_id = t.id
             ORDER BY c.through_sequence DESC LIMIT 1) AS active_compaction_id
            FROM threads t WHERE t.workspace_id = ? AND t.id = ?""",
            (workspace_id, thread_id),
        )
        return self._thread(row) if row else None

    def list_threads(self, workspace_id: str) -> list[dict[str, Any]]:
        rows = self._all(
            """SELECT t.*,
            (SELECT c.id FROM thread_compactions c
             WHERE c.workspace_id = t.workspace_id AND c.thread_id = t.id
             ORDER BY c.through_sequence DESC LIMIT 1) AS active_compaction_id
            FROM threads t WHERE t.workspace_id = ? ORDER BY t.updated_at DESC""",
            (workspace_id,),
        )
        return [self._thread(row) for row in rows]

    def _customization_run_snapshot_locked(
        self,
        workspace_id: str,
        thread_id: str,
        agent_version_id: str,
    ) -> dict[str, Any]:
        """Freeze mutable customization selectors while the Run transaction owns locks."""
        configuration = self._thread_configuration_locked(
            workspace_id,
            thread_id,
            for_share=True,
        )
        if configuration is None:
            raise RepositoryConflict("Thread customization is unavailable")
        if str(configuration["agent_version_id"]) != agent_version_id:
            raise RepositoryConflict("Thread customization does not match the Run Agent")

        active_skills = self._all(
            """SELECT skill_version_id FROM thread_configuration_skills
            WHERE workspace_id = ? AND thread_id = ? ORDER BY position""",
            (workspace_id, thread_id),
        )
        manual_rules = self._all(
            """SELECT rule_version_id FROM thread_configuration_rules
            WHERE workspace_id = ? AND thread_id = ? ORDER BY position""",
            (workspace_id, thread_id),
        )

        workspace = self._one(
            "SELECT id FROM workspaces WHERE id = ? FOR SHARE",
            (workspace_id,),
        )
        if workspace is None:
            raise RepositoryConflict("Workspace does not exist")
        preferences = self._one(
            """SELECT revision, content, content_sha256 FROM workspace_preferences
            WHERE workspace_id = ? FOR SHARE""",
            (workspace_id,),
        )
        preference_snapshot = {
            "revision": int(preferences["revision"]) if preferences else 0,
            "content": str(preferences["content"]) if preferences else "",
            "content_sha256": (
                str(preferences["content_sha256"])
                if preferences
                else self._empty_digest()
            ),
        }

        agent_skills = self._all(
            """SELECT b.skill_version_id, b.mode, b.position
            FROM agent_version_skill_bindings b
            JOIN skills s
              ON s.workspace_id = b.workspace_id
            JOIN skill_versions sv
              ON sv.workspace_id = b.workspace_id
             AND sv.id = b.skill_version_id
             AND sv.skill_id = s.id
            WHERE b.workspace_id = ? AND b.agent_version_id = ?
              AND s.enabled = TRUE
            ORDER BY b.position
            FOR SHARE OF s""",
            (workspace_id, agent_version_id),
        )
        agent_rules = self._all(
            """SELECT b.rule_version_id, b.position
            FROM agent_version_rule_bindings b
            JOIN rules r
              ON r.workspace_id = b.workspace_id
            JOIN rule_versions rv
              ON rv.workspace_id = b.workspace_id
             AND rv.id = b.rule_version_id
             AND rv.rule_id = r.id
            WHERE b.workspace_id = ? AND b.agent_version_id = ?
              AND r.enabled = TRUE
            ORDER BY b.position
            FOR SHARE OF r""",
            (workspace_id, agent_version_id),
        )
        workspace_rules = self._all(
            """SELECT current_version_id AS rule_version_id FROM rules
            WHERE workspace_id = ? AND scope = 'workspace' AND enabled = TRUE
            ORDER BY id FOR SHARE""",
            (workspace_id,),
        )
        thread_rules = self._all(
            """SELECT current_version_id AS rule_version_id FROM rules
            WHERE workspace_id = ? AND scope = 'thread' AND thread_id = ?
              AND enabled = TRUE
            ORDER BY id FOR SHARE""",
            (workspace_id, thread_id),
        )
        return {
            "schema": 1,
            "thread_configuration_revision": int(configuration["revision"]),
            "active_skill_version_ids": [
                str(item["skill_version_id"]) for item in active_skills
            ],
            "manual_rule_version_ids": [
                str(item["rule_version_id"]) for item in manual_rules
            ],
            "workspace_preferences": preference_snapshot,
            "agent_skill_bindings": [
                {
                    "skill_version_id": str(item["skill_version_id"]),
                    "mode": str(item["mode"]),
                    "position": int(item["position"]),
                }
                for item in agent_skills
            ],
            "agent_rule_version_ids": [
                str(item["rule_version_id"]) for item in agent_rules
            ],
            "workspace_rule_version_ids": [
                str(item["rule_version_id"]) for item in workspace_rules
            ],
            "thread_rule_version_ids": [
                str(item["rule_version_id"]) for item in thread_rules
            ],
        }

    def create_attachment(
        self,
        workspace_id: str,
        *,
        upload_id: str,
        name: str,
        media_type: str,
        kind: str,
        content: bytes,
        sha256: str,
        expires_at: str,
        extracted_text: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        attachment_id, created_at = new_id("att"), utc_now()
        if not upload_id.strip() or len(upload_id) > 160:
            raise ValueError("upload_id must contain between 1 and 160 characters")
        if not content:
            raise ValueError("attachment content cannot be empty")
        if hashlib.sha256(content).hexdigest() != sha256:
            raise ValueError("attachment sha256 does not match its content")
        if kind not in {"image", "document"}:
            raise ValueError("attachment kind is unsupported")
        metadata_json = self._json(metadata or {})
        with self.lock, self.connection:
            inserted = self._one(
                """INSERT INTO attachments
                (id, workspace_id, upload_id, name, media_type, kind, size_bytes,
                 sha256, status, metadata_json, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?, ?)
                ON CONFLICT (workspace_id, upload_id) DO NOTHING
                RETURNING id""",
                (
                    attachment_id,
                    workspace_id,
                    upload_id,
                    name,
                    media_type,
                    kind,
                    len(content),
                    sha256,
                    metadata_json,
                    created_at,
                    expires_at,
                ),
            )
            if inserted is None:
                existing = self._one(
                    """SELECT a.*,
                    EXISTS(SELECT 1 FROM message_attachments ma
                           WHERE ma.attachment_id = a.id) AS bound
                    FROM attachments a
                    WHERE a.workspace_id = ? AND a.upload_id = ? FOR UPDATE""",
                    (workspace_id, upload_id),
                )
                if existing is None:
                    raise RepositoryConflict("Attachment upload could not be resolved")
                if (
                    str(existing["sha256"]) != sha256
                    or int(existing["size_bytes"]) != len(content)
                    or str(existing["media_type"]) != media_type
                    or str(existing["name"]) != name
                ):
                    raise RepositoryConflict(
                        "upload_id is already bound to different attachment content"
                    )
                return self._attachment(existing)
            self.connection.execute(
                """INSERT INTO attachment_blobs
                (attachment_id, workspace_id, content, extracted_text)
                VALUES (?, ?, ?, ?)""",
                (attachment_id, workspace_id, content, extracted_text),
            )
        return self.get_attachment(workspace_id, attachment_id) or {}

    def get_attachment(
        self,
        workspace_id: str,
        attachment_id: str,
    ) -> dict[str, Any] | None:
        row = self._one(
            """SELECT a.*,
            EXISTS(SELECT 1 FROM message_attachments ma
                   WHERE ma.attachment_id = a.id) AS bound
            FROM attachments a WHERE a.workspace_id = ? AND a.id = ?""",
            (workspace_id, attachment_id),
        )
        return self._attachment(row) if row else None

    def get_attachment_blob(
        self,
        workspace_id: str,
        attachment_id: str,
    ) -> dict[str, Any] | None:
        row = self._one(
            """SELECT a.id, a.workspace_id, a.name, a.media_type, a.kind,
            a.size_bytes, a.sha256, a.status, a.expires_at, b.content,
            b.extracted_text
            FROM attachments a
            JOIN attachment_blobs b
              ON b.workspace_id = a.workspace_id AND b.attachment_id = a.id
            WHERE a.workspace_id = ? AND a.id = ?""",
            (workspace_id, attachment_id),
        )
        if row is None:
            return None
        result = dict(row)
        raw = result.get("content")
        result["content"] = bytes(raw) if raw is not None else b""
        return result

    def list_message_attachments(
        self,
        workspace_id: str,
        message_id: str,
    ) -> list[dict[str, Any]]:
        rows = self._all(
            """SELECT a.*, TRUE AS bound, ma.message_id, ma.position
            FROM message_attachments ma
            JOIN attachments a
              ON a.workspace_id = ma.workspace_id AND a.id = ma.attachment_id
            WHERE ma.workspace_id = ? AND ma.message_id = ?
            ORDER BY ma.position""",
            (workspace_id, message_id),
        )
        return [self._attachment(row) for row in rows]

    def delete_attachment(self, workspace_id: str, attachment_id: str) -> bool:
        with self.lock, self.connection:
            row = self._one(
                """SELECT a.id,
                EXISTS(SELECT 1 FROM message_attachments ma
                       WHERE ma.attachment_id = a.id) AS bound
                FROM attachments a
                WHERE a.workspace_id = ? AND a.id = ? FOR UPDATE""",
                (workspace_id, attachment_id),
            )
            if row is None:
                return False
            if bool(row["bound"]):
                raise RepositoryConflict("Bound attachments cannot be deleted")
            self.connection.execute(
                "DELETE FROM attachments WHERE workspace_id = ? AND id = ?",
                (workspace_id, attachment_id),
            )
        return True

    @staticmethod
    def _validate_artifact_fields(
        *,
        title: str,
        kind: str,
        content_type: str,
        content: str,
    ) -> tuple[str, str]:
        normalized_title = title.strip()
        if not normalized_title or len(normalized_title) > 200:
            raise ValueError("Artifact title must contain between 1 and 200 characters")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", kind):
            raise ValueError("Artifact kind is invalid")
        if content_type not in {"text/markdown", "text/plain", "application/json", "text/html"}:
            raise ValueError("Artifact content_type is unsupported")
        if len(content) > 500_000:
            raise ValueError("Artifact content exceeds 500000 characters")
        if content_type == "application/json":
            try:
                json.loads(content)
            except json.JSONDecodeError as exc:
                raise ValueError("Artifact application/json content is invalid") from exc
        return normalized_title, hashlib.sha256(content.encode("utf-8")).hexdigest()

    def append_artifact_event(
        self,
        workspace_id: str,
        run_id: str,
        artifact: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist a generated Artifact and its canonical Run event atomically."""
        title = str(artifact.get("title") or "")
        kind = str(artifact.get("kind") or "document")
        content_type = str(artifact.get("content_type") or "text/markdown")
        content = str(artifact.get("content") or "")
        generation_key = str(artifact.get("generation_key") or "main")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", generation_key):
            raise ValueError("Artifact generation key is invalid")
        title, content_sha256 = self._validate_artifact_fields(
            title=title,
            kind=kind,
            content_type=content_type,
            content=content,
        )
        now = utc_now()
        with self.lock, self.connection:
            run = self._one(
                """SELECT thread_id, status FROM runs
                WHERE workspace_id = ? AND id = ? FOR UPDATE""",
                (workspace_id, run_id),
            )
            if run is None:
                raise RepositoryConflict("Run does not exist in this Workspace")
            if str(run["status"]) in {"completed", "failed", "cancelled"}:
                raise RepositoryConflict("A terminal Run cannot update its Artifact")
            existing = self._one(
                """SELECT * FROM artifacts
                WHERE workspace_id = ? AND source_run_id = ? AND generation_key = ? FOR UPDATE""",
                (workspace_id, run_id, generation_key),
            )
            if existing is None:
                artifact_id = new_id("art")
                version = 1
                created_at = now
                self.connection.execute(
                    """INSERT INTO artifacts
                    (id, workspace_id, thread_id, source_run_id, title, kind,
                     content_type, version, content, content_sha256, created_at, updated_at, generation_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        artifact_id,
                        workspace_id,
                        run["thread_id"],
                        run_id,
                        title,
                        kind,
                        content_type,
                        version,
                        content,
                        content_sha256,
                        created_at,
                        now,
                        generation_key,
                    ),
                )
                changed = True
            else:
                artifact_id = str(existing["id"])
                version = int(existing["version"])
                created_at = str(existing["created_at"])
                changed = any(
                    (
                        str(existing["title"]) != title,
                        str(existing["kind"]) != kind,
                        str(existing["content_type"]) != content_type,
                        str(existing["content_sha256"]) != content_sha256,
                        str(existing["content"]) != content,
                    )
                )
                if changed:
                    version += 1
                    self.connection.execute(
                        """UPDATE artifacts
                        SET title = ?, kind = ?, content_type = ?, version = ?,
                            content = ?, content_sha256 = ?, updated_at = ?
                        WHERE workspace_id = ? AND id = ?""",
                        (
                            title,
                            kind,
                            content_type,
                            version,
                            content,
                            content_sha256,
                            now,
                            workspace_id,
                            artifact_id,
                        ),
                    )
            if changed:
                self.connection.execute(
                    """INSERT INTO artifact_versions
                    (artifact_id, workspace_id, version, title, kind, content_type,
                     content, content_sha256, source, source_run_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'runtime', ?, ?)""",
                    (
                        artifact_id,
                        workspace_id,
                        version,
                        title,
                        kind,
                        content_type,
                        content,
                        content_sha256,
                        run_id,
                        now,
                    ),
                )
            artifact_row = self._one(
                "SELECT * FROM artifacts WHERE workspace_id = ? AND id = ?",
                (workspace_id, artifact_id),
            )
            if artifact_row is None:
                raise RepositoryConflict("Artifact persistence failed")
            public_artifact = self._artifact(artifact_row)
            if not changed:
                previous_event = self._one(
                    """SELECT id, sequence, created_at FROM events
                    WHERE workspace_id = ? AND run_id = ?
                      AND type = 'artifact.updated'
                      AND CAST(payload_json AS jsonb)->'artifact'->>'id' = ?
                    ORDER BY sequence DESC LIMIT 1""",
                    (workspace_id, run_id, artifact_id),
                )
                if previous_event is None:
                    raise RepositoryConflict(
                        "Artifact exists without its canonical event"
                    )
                return {
                    "id": previous_event["id"],
                    "run_id": run_id,
                    "sequence": int(previous_event["sequence"]),
                    "type": "artifact.updated",
                    "timestamp": previous_event["created_at"],
                    "payload": {"artifact": public_artifact},
                }
            sequence_row = self._one(
                """UPDATE runs
                SET next_event_sequence = next_event_sequence + 1
                WHERE workspace_id = ? AND id = ?
                RETURNING next_event_sequence AS value""",
                (workspace_id, run_id),
            )
            if sequence_row is None:
                raise RepositoryConflict("Run does not exist in this Workspace")
            sequence = int(sequence_row["value"])
            event_id = new_id("evt")
            payload = {"artifact": public_artifact}
            self.connection.execute(
                """INSERT INTO events
                (id, workspace_id, run_id, sequence, type, payload_json, created_at)
                VALUES (?, ?, ?, ?, 'artifact.updated', ?, ?)""",
                (
                    event_id,
                    workspace_id,
                    run_id,
                    sequence,
                    self._json(payload),
                    now,
                ),
            )
        return {
            "id": event_id,
            "run_id": run_id,
            "sequence": sequence,
            "type": "artifact.updated",
            "timestamp": now,
            "payload": payload,
        }

    def get_artifact(
        self,
        workspace_id: str,
        artifact_id: str,
    ) -> dict[str, Any] | None:
        row = self._one(
            "SELECT * FROM artifacts WHERE workspace_id = ? AND id = ?",
            (workspace_id, artifact_id),
        )
        return self._artifact(row) if row else None

    def list_thread_artifacts(
        self,
        workspace_id: str,
        thread_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        bounded_limit = min(max(limit, 1), 100)
        rows = self._all(
            """SELECT * FROM artifacts
            WHERE workspace_id = ? AND thread_id = ?
            ORDER BY updated_at DESC, id DESC LIMIT ?""",
            (workspace_id, thread_id, bounded_limit),
        )
        return [self._artifact(row) for row in rows]

    def update_artifact(
        self,
        workspace_id: str,
        artifact_id: str,
        *,
        expected_version: int,
        title: str | None = None,
        content: str | None = None,
    ) -> dict[str, Any] | None:
        if expected_version < 1:
            raise ValueError("expected_version must be positive")
        now = utc_now()
        with self.lock, self.connection:
            candidate = self._one(
                """SELECT source_run_id FROM artifacts
                WHERE workspace_id = ? AND id = ?""",
                (workspace_id, artifact_id),
            )
            if candidate is None:
                return None
            source_run = self._one(
                """SELECT r.status,
                EXISTS(
                    SELECT 1 FROM events e
                    WHERE e.workspace_id = r.workspace_id AND e.run_id = r.id
                      AND e.type IN ('run.completed', 'run.failed')
                ) AS has_terminal_event
                FROM runs r
                WHERE r.workspace_id = ? AND r.id = ? FOR SHARE""",
                (workspace_id, candidate["source_run_id"]),
            )
            if source_run is None:
                raise RepositoryConflict("Artifact source Run is unavailable")
            if str(source_run["status"]) not in {
                "completed",
                "failed",
                "cancelled",
            }:
                raise RepositoryConflict(
                    "Artifact can only be edited after its source Run is terminal"
                )
            if not bool(source_run["has_terminal_event"]):
                raise RepositoryConflict(
                    "Artifact cannot be edited before its source Run terminal event"
                )
            existing = self._one(
                """SELECT * FROM artifacts
                WHERE workspace_id = ? AND id = ? FOR UPDATE""",
                (workspace_id, artifact_id),
            )
            if existing is None:
                return None
            current_version = int(existing["version"])
            if current_version != expected_version:
                raise ArtifactVersionConflict(current_version)
            next_title = str(existing["title"]) if title is None else title
            next_content = str(existing["content"]) if content is None else content
            next_title, content_sha256 = self._validate_artifact_fields(
                title=next_title,
                kind=str(existing["kind"]),
                content_type=str(existing["content_type"]),
                content=next_content,
            )
            if (
                next_title == str(existing["title"])
                and next_content == str(existing["content"])
            ):
                return self._artifact(existing)
            next_version = current_version + 1
            self.connection.execute(
                """UPDATE artifacts
                SET title = ?, version = ?, content = ?, content_sha256 = ?, updated_at = ?
                WHERE workspace_id = ? AND id = ?""",
                (
                    next_title,
                    next_version,
                    next_content,
                    content_sha256,
                    now,
                    workspace_id,
                    artifact_id,
                ),
            )
            self.connection.execute(
                """INSERT INTO artifact_versions
                (artifact_id, workspace_id, version, title, kind, content_type,
                 content, content_sha256, source, source_run_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'user', ?, ?)""",
                (
                    artifact_id,
                    workspace_id,
                    next_version,
                    next_title,
                    existing["kind"],
                    existing["content_type"],
                    next_content,
                    content_sha256,
                    existing["source_run_id"],
                    now,
                ),
            )
            updated = self._one(
                "SELECT * FROM artifacts WHERE workspace_id = ? AND id = ?",
                (workspace_id, artifact_id),
            )
        return self._artifact(updated) if updated else None

    def create_run(
        self,
        workspace_id: str,
        thread_id: str,
        agent_version_id: str,
        prompt: str,
        *,
        message_parts: list[dict[str, Any]] | None = None,
        estimated_tokens: int | None = None,
        attachment_ids: tuple[str, ...] = (),
        max_total_attachment_bytes: int = 20 * 1024 * 1024,
    ) -> dict[str, Any]:
        run_id, message_id, created_at = new_id("run"), new_id("msg"), utc_now()
        parts = message_parts or ([{"type": "text", "text": prompt}] if prompt else [])
        self._validate_persisted_parts(parts)
        if len(attachment_ids) > 4:
            raise AttachmentBindingError(
                "too_many_attachments",
                "A Run can bind at most four attachments",
            )
        if len(attachment_ids) != len(set(attachment_ids)):
            raise AttachmentBindingError(
                "duplicate_attachment",
                "A Run cannot bind the same attachment more than once",
            )
        persisted_attachment_ids = tuple(
            str(part.get("attachment_id"))
            for part in parts
            if part.get("type") == "attachment" and part.get("attachment_id")
        )
        if persisted_attachment_ids != attachment_ids:
            raise ValueError("message attachment references must match attachment_ids")
        estimate = (
            self._estimate_tokens(prompt)
            if estimated_tokens is None
            else estimated_tokens
        )
        if estimate < 0:
            raise ValueError("estimated_tokens cannot be negative")
        with self.lock, self.connection:
            thread = self._one(
                """SELECT id, agent_id, agent_version_id FROM threads
                WHERE workspace_id = ? AND id = ? FOR UPDATE""",
                (workspace_id, thread_id),
            )
            if thread is None:
                raise RepositoryConflict("Thread does not exist in this Workspace")
            if agent_version_id != thread["agent_version_id"]:
                raise RepositoryConflict(
                    "Run Agent version must match the immutable Thread version"
                )
            active = self._one(
                """SELECT id FROM runs WHERE workspace_id = ? AND thread_id = ?
                AND status IN ('queued', 'running', 'waiting_for_approval') LIMIT 1""",
                (workspace_id, thread_id),
            )
            if active is not None:
                raise RepositoryConflict("Thread already has an active Run")
            attachment_rows: list[Mapping[str, Any]] = []
            if attachment_ids:
                placeholders = ",".join("?" for _ in attachment_ids)
                attachment_rows = self._all(
                    f"""SELECT a.*,
                    EXISTS(SELECT 1 FROM message_attachments ma
                           WHERE ma.attachment_id = a.id) AS bound
                    FROM attachments a
                    WHERE a.workspace_id = ? AND a.id IN ({placeholders})
                    FOR UPDATE""",
                    (workspace_id, *attachment_ids),
                )
                by_id = {str(row["id"]): row for row in attachment_rows}
                if len(by_id) != len(attachment_ids):
                    raise AttachmentBindingError(
                        "attachment_unavailable",
                        "One or more attachments are unavailable in this Workspace",
                    )
                attachment_rows = [by_id[item] for item in attachment_ids]
                if any(str(row["status"]) != "ready" for row in attachment_rows):
                    raise AttachmentBindingError(
                        "attachment_not_ready",
                        "One or more attachments are not ready",
                    )
                if any(bool(row["bound"]) for row in attachment_rows):
                    raise AttachmentBindingError(
                        "attachment_already_bound",
                        "An attachment can only be bound to one user message",
                    )
                if any(str(row["expires_at"]) <= created_at for row in attachment_rows):
                    raise AttachmentBindingError(
                        "attachment_expired",
                        "One or more staged attachments have expired",
                    )
                if sum(int(row["size_bytes"]) for row in attachment_rows) > max_total_attachment_bytes:
                    raise AttachmentBindingError(
                        "attachment_total_too_large",
                        "Run attachments exceed the total size limit",
                    )
            customization_snapshot = self._customization_run_snapshot_locked(
                workspace_id,
                thread_id,
                agent_version_id,
            )
            snapshot_json = json.dumps(
                customization_snapshot,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            snapshot_sha256 = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
            sequence_row = self._one(
                """UPDATE threads
                SET next_message_sequence = next_message_sequence + 1, updated_at = ?
                WHERE workspace_id = ? AND id = ?
                RETURNING next_message_sequence AS value""",
                (created_at, workspace_id, thread_id),
            )
            if sequence_row is None:
                raise RepositoryConflict("Thread does not exist in this Workspace")
            self.connection.execute(
                """INSERT INTO runs
                (id, workspace_id, thread_id, agent_version_id, status, input, created_at)
                VALUES (?, ?, ?, ?, 'queued', ?, ?)""",
                (run_id, workspace_id, thread_id, agent_version_id, prompt, created_at),
            )
            self.connection.execute(
                """INSERT INTO run_customization_snapshots
                (run_id, workspace_id, snapshot_json, snapshot_sha256, created_at)
                VALUES (?, ?, ?, ?, ?)""",
                (
                    run_id,
                    workspace_id,
                    snapshot_json,
                    snapshot_sha256,
                    created_at,
                ),
            )
            self.connection.execute(
                """INSERT INTO messages
                (id, workspace_id, thread_id, run_id, agent_version_id, sequence, role,
                 status, parts_json, estimated_tokens, created_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, 'user', 'completed', ?, ?, ?, ?)""",
                (
                    message_id,
                    workspace_id,
                    thread_id,
                    run_id,
                    agent_version_id,
                    int(sequence_row["value"]),
                    self._json(parts),
                    estimate,
                    created_at,
                    created_at,
                ),
            )
            for position, attachment_id in enumerate(attachment_ids):
                self.connection.execute(
                    """INSERT INTO message_attachments
                    (message_id, attachment_id, workspace_id, position, created_at)
                    VALUES (?, ?, ?, ?, ?)""",
                    (
                        message_id,
                        attachment_id,
                        workspace_id,
                        position,
                        created_at,
                    ),
                )
        return self.get_run(workspace_id, run_id) or {}

    def create_run_with_messages(
        self,
        workspace_id: str,
        thread_id: str,
        agent_version_id: str,
        prompt: str,
        parts: list[dict[str, Any]],
        estimated_tokens: int,
        *,
        attachment_ids: tuple[str, ...] = (),
        max_total_attachment_bytes: int = 20 * 1024 * 1024,
    ) -> dict[str, Any]:
        """Explicit conversation-kernel entrypoint used by API composition."""
        return self.create_run(
            workspace_id,
            thread_id,
            agent_version_id,
            prompt,
            message_parts=parts,
            estimated_tokens=estimated_tokens,
            attachment_ids=attachment_ids,
            max_total_attachment_bytes=max_total_attachment_bytes,
        )

    def list_messages(
        self,
        workspace_id: str,
        thread_id: str,
        *,
        after: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        bounded_limit = min(max(limit, 1), 500)
        rows = self._all(
            """SELECT * FROM messages
            WHERE workspace_id = ? AND thread_id = ? AND sequence > ?
            ORDER BY sequence LIMIT ?""",
            (workspace_id, thread_id, after, bounded_limit),
        )
        return [self._message(row) for row in rows]

    def get_message(
        self,
        workspace_id: str,
        message_id: str,
    ) -> dict[str, Any] | None:
        row = self._one(
            "SELECT * FROM messages WHERE workspace_id = ? AND id = ?",
            (workspace_id, message_id),
        )
        return self._message(row) if row else None

    def complete_run_with_assistant_message(
        self,
        workspace_id: str,
        run_id: str,
        parts: list[dict[str, Any]],
        *,
        estimated_tokens: int,
    ) -> dict[str, Any]:
        return self.finalize_run_with_assistant_message(
            workspace_id,
            run_id,
            "completed",
            parts,
            estimated_tokens=estimated_tokens,
        )

    def finalize_run_with_assistant_message(
        self,
        workspace_id: str,
        run_id: str,
        status: str,
        parts: list[dict[str, Any]],
        *,
        estimated_tokens: int,
    ) -> dict[str, Any]:
        """Persist a completed response or safe failed partial exactly once."""
        if status not in {"completed", "failed"}:
            raise ValueError("Assistant message status must be completed or failed")
        if estimated_tokens < 0:
            raise ValueError("estimated_tokens cannot be negative")
        self._validate_persisted_parts(parts)
        completed_at = utc_now()
        with self.lock, self.connection:
            run = self._one(
                """SELECT * FROM runs
                WHERE workspace_id = ? AND id = ? FOR UPDATE""",
                (workspace_id, run_id),
            )
            if run is None:
                raise RepositoryConflict("Run does not exist in this Workspace")
            existing = self._one(
                """SELECT * FROM messages
                WHERE workspace_id = ? AND run_id = ? AND role = 'assistant'""",
                (workspace_id, run_id),
            )
            if existing is not None:
                materialized = self._message(existing)
                if materialized["status"] != status or materialized["parts"] != parts:
                    raise RepositoryConflict(
                        "Run already has a different immutable assistant message"
                    )
                return materialized
            if run["status"] in {"completed", "failed", "cancelled"} and run[
                "status"
            ] != status:
                raise RepositoryConflict(
                    f"Cannot finalize a Run in terminal status {run['status']} as {status}"
                )
            sequence_row = self._one(
                """UPDATE threads
                SET next_message_sequence = next_message_sequence + 1, updated_at = ?
                WHERE workspace_id = ? AND id = ?
                RETURNING next_message_sequence AS value""",
                (completed_at, workspace_id, run["thread_id"]),
            )
            if sequence_row is None:
                raise RepositoryConflict("Run Thread does not exist in this Workspace")
            message_id = new_id("msg")
            self.connection.execute(
                """INSERT INTO messages
                (id, workspace_id, thread_id, run_id, agent_version_id, sequence, role,
                 status, parts_json, estimated_tokens, created_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, 'assistant', ?, ?, ?, ?, ?)""",
                (
                    message_id,
                    workspace_id,
                    run["thread_id"],
                    run_id,
                    run["agent_version_id"],
                    int(sequence_row["value"]),
                    status,
                    self._json(parts),
                    estimated_tokens,
                    completed_at,
                    completed_at,
                ),
            )
            self.connection.execute(
                """UPDATE runs SET status = ?, completed_at = ?
                WHERE workspace_id = ? AND id = ?""",
                (status, completed_at, workspace_id, run_id),
            )
            inserted = self._one(
                "SELECT * FROM messages WHERE workspace_id = ? AND id = ?",
                (workspace_id, message_id),
            )
        return self._message(inserted) if inserted else {}

    def finalize_assistant_message(
        self,
        workspace_id: str,
        run_id: str,
        status: str,
        content: str,
        estimated_tokens: int,
    ) -> dict[str, Any]:
        """Finalize visible content without exposing provider-specific message parts."""
        return self.finalize_run_with_assistant_message(
            workspace_id,
            run_id,
            status,
            [{"type": "text", "text": content}],
            estimated_tokens=estimated_tokens,
        )

    def finalize_run_with_event(
        self,
        workspace_id: str,
        run_id: str,
        status: str,
        content: str,
        estimated_tokens: int,
        terminal_event_type: str,
        terminal_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically persist visible output, terminal Run state, and terminal event."""
        expected_event_type = {
            "completed": "run.completed",
            "failed": "run.failed",
        }.get(status)
        if expected_event_type is None or terminal_event_type != expected_event_type:
            raise ValueError("Terminal Run status and event type must match")
        if estimated_tokens < 0:
            raise ValueError("estimated_tokens cannot be negative")
        parts = [{"type": "text", "text": content}]
        if content.strip():
            self._validate_persisted_parts(parts)
        completed_at = utc_now()
        with self.lock, self.connection:
            run = self._one(
                """SELECT * FROM runs
                WHERE workspace_id = ? AND id = ? FOR UPDATE""",
                (workspace_id, run_id),
            )
            if run is None:
                raise RepositoryConflict("Run does not exist in this Workspace")
            if str(run["status"]) in {"completed", "failed", "cancelled"} and str(
                run["status"]
            ) != status:
                raise RepositoryConflict(
                    f"Cannot finalize a Run in terminal status {run['status']} as {status}"
                )

            existing_message = self._one(
                """SELECT * FROM messages
                WHERE workspace_id = ? AND run_id = ? AND role = 'assistant'""",
                (workspace_id, run_id),
            )
            message_id: str | None = None
            if existing_message is not None:
                materialized = self._message(existing_message)
                if materialized["status"] != status:
                    raise RepositoryConflict(
                        "Run already has an assistant message with another status"
                    )
                if content.strip() and materialized["parts"] != parts:
                    raise RepositoryConflict(
                        "Run already has a different immutable assistant message"
                    )
                message_id = str(materialized["id"])
            elif content.strip():
                sequence_row = self._one(
                    """UPDATE threads
                    SET next_message_sequence = next_message_sequence + 1, updated_at = ?
                    WHERE workspace_id = ? AND id = ?
                    RETURNING next_message_sequence AS value""",
                    (completed_at, workspace_id, run["thread_id"]),
                )
                if sequence_row is None:
                    raise RepositoryConflict(
                        "Run Thread does not exist in this Workspace"
                    )
                message_id = new_id("msg")
                self.connection.execute(
                    """INSERT INTO messages
                    (id, workspace_id, thread_id, run_id, agent_version_id, sequence,
                     role, status, parts_json, estimated_tokens, created_at, completed_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'assistant', ?, ?, ?, ?, ?)""",
                    (
                        message_id,
                        workspace_id,
                        run["thread_id"],
                        run_id,
                        run["agent_version_id"],
                        int(sequence_row["value"]),
                        status,
                        self._json(parts),
                        estimated_tokens,
                        completed_at,
                        completed_at,
                    ),
                )

            existing_event = self._one(
                """SELECT * FROM events
                WHERE workspace_id = ? AND run_id = ?
                  AND type IN ('run.completed', 'run.failed')
                ORDER BY sequence DESC LIMIT 1""",
                (workspace_id, run_id),
            )
            context = self._one(
                """SELECT id FROM run_context_assemblies
                WHERE workspace_id = ? AND run_id = ?""",
                (workspace_id, run_id),
            )
            payload = {
                **terminal_payload,
                "output_message_id": message_id,
                "context_assembly_id": context["id"] if context else None,
            }
            if existing_event is not None:
                persisted_payload = self._decoded_json(existing_event["payload_json"])
                if (
                    str(existing_event["type"]) != terminal_event_type
                    or persisted_payload != payload
                ):
                    raise RepositoryConflict(
                        "Run already has a different immutable terminal event"
                    )
                return {
                    "id": existing_event["id"],
                    "run_id": run_id,
                    "sequence": int(existing_event["sequence"]),
                    "type": terminal_event_type,
                    "timestamp": existing_event["created_at"],
                    "payload": payload,
                }

            self.connection.execute(
                """UPDATE runs SET status = ?, completed_at = ?
                WHERE workspace_id = ? AND id = ?""",
                (status, completed_at, workspace_id, run_id),
            )
            sequence_row = self._one(
                """UPDATE runs
                SET next_event_sequence = next_event_sequence + 1
                WHERE workspace_id = ? AND id = ?
                RETURNING next_event_sequence AS value""",
                (workspace_id, run_id),
            )
            if sequence_row is None:
                raise RepositoryConflict("Run does not exist in this Workspace")
            event_id = new_id("evt")
            sequence = int(sequence_row["value"])
            self.connection.execute(
                """INSERT INTO events
                (id, workspace_id, run_id, sequence, type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    workspace_id,
                    run_id,
                    sequence,
                    terminal_event_type,
                    self._json(payload),
                    completed_at,
                ),
            )
        return {
            "id": event_id,
            "run_id": run_id,
            "sequence": sequence,
            "type": terminal_event_type,
            "timestamp": completed_at,
            "payload": payload,
        }

    def create_thread_compaction(
        self,
        workspace_id: str,
        thread_id: str,
        *,
        through_sequence: int,
        summary: str,
        source_message_ids: list[str],
        source_digest: str,
        estimated_source_tokens: int,
        estimated_summary_tokens: int,
        strategy: str,
        created_by_run_id: str | None = None,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        if through_sequence < 1 or not source_message_ids or not summary.strip():
            raise ValueError("Compaction requires a source prefix and non-empty summary")
        if estimated_source_tokens < 0 or estimated_summary_tokens < 0:
            raise ValueError("Compaction token counts cannot be negative")
        compaction_id, created_at = new_id("cmp"), utc_now()
        try:
            with self.lock, self.connection:
                thread = self._one(
                    """SELECT next_message_sequence FROM threads
                    WHERE workspace_id = ? AND id = ? FOR UPDATE""",
                    (workspace_id, thread_id),
                )
                if thread is None:
                    raise RepositoryConflict("Thread does not exist in this Workspace")
                if through_sequence > int(thread["next_message_sequence"]):
                    raise RepositoryConflict("Compaction exceeds the Thread message surface")
                rows = self._all(
                    """SELECT id, sequence FROM messages
                    WHERE workspace_id = ? AND thread_id = ? AND sequence <= ?
                    ORDER BY sequence""",
                    (workspace_id, thread_id, through_sequence),
                )
                actual_ids = [str(row["id"]) for row in rows]
                if actual_ids != source_message_ids:
                    raise RepositoryConflict(
                        "Compaction sources must be the exact immutable Thread prefix"
                    )
                if parent_id:
                    parent = self._one(
                        """SELECT id FROM thread_compactions
                        WHERE workspace_id = ? AND thread_id = ? AND id = ?""",
                        (workspace_id, thread_id, parent_id),
                    )
                    if parent is None:
                        raise RepositoryConflict(
                            "Parent compaction does not belong to this Thread"
                        )
                if created_by_run_id:
                    source_run = self._one(
                        """SELECT id FROM runs
                        WHERE workspace_id = ? AND thread_id = ? AND id = ?""",
                        (workspace_id, thread_id, created_by_run_id),
                    )
                    if source_run is None:
                        raise RepositoryConflict(
                            "Compaction Run does not belong to this Thread"
                        )
                self.connection.execute(
                    """INSERT INTO thread_compactions
                    (id, workspace_id, thread_id, parent_id, through_sequence, summary,
                     source_digest, source_message_ids_json, estimated_source_tokens,
                     estimated_summary_tokens, strategy, created_by_run_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        compaction_id,
                        workspace_id,
                        thread_id,
                        parent_id,
                        through_sequence,
                        summary.strip(),
                        source_digest,
                        self._json(source_message_ids),
                        estimated_source_tokens,
                        estimated_summary_tokens,
                        strategy,
                        created_by_run_id,
                        created_at,
                    ),
                )
        except RepositoryConflict:
            raise
        except Exception as exc:
            if self._is_unique_violation(
                exc,
                postgres_constraint="thread_compactions_thread_sequence_key",
            ):
                existing = self._one(
                    """SELECT * FROM thread_compactions
                    WHERE workspace_id = ? AND thread_id = ? AND through_sequence = ?""",
                    (workspace_id, thread_id, through_sequence),
                )
                if existing is not None:
                    materialized = self._compaction(existing)
                    expected = {
                        "parent_id": parent_id,
                        "summary": summary.strip(),
                        "source_digest": source_digest,
                        "source_message_ids": source_message_ids,
                        "estimated_source_tokens": estimated_source_tokens,
                        "estimated_summary_tokens": estimated_summary_tokens,
                        "strategy": strategy,
                        "created_by_run_id": created_by_run_id,
                    }
                    if any(materialized[key] != value for key, value in expected.items()):
                        raise RepositoryConflict(
                            "Thread prefix already has a different immutable compaction"
                        ) from exc
                    return materialized
            raise
        created = self._one(
            "SELECT * FROM thread_compactions WHERE workspace_id = ? AND id = ?",
            (workspace_id, compaction_id),
        )
        return self._compaction(created) if created else {}

    def latest_thread_compaction(
        self,
        workspace_id: str,
        thread_id: str,
    ) -> dict[str, Any] | None:
        row = self._one(
            """SELECT * FROM thread_compactions
            WHERE workspace_id = ? AND thread_id = ?
            ORDER BY through_sequence DESC LIMIT 1""",
            (workspace_id, thread_id),
        )
        return self._compaction(row) if row else None

    def get_active_compaction(
        self,
        workspace_id: str,
        thread_id: str,
    ) -> dict[str, Any] | None:
        return self.latest_thread_compaction(workspace_id, thread_id)

    def create_compaction(
        self,
        workspace_id: str,
        thread_id: str,
        **payload: Any,
    ) -> dict[str, Any]:
        return self.create_thread_compaction(workspace_id, thread_id, **payload)

    def save_run_context_assembly(
        self,
        workspace_id: str,
        run_id: str,
        *,
        entries: list[dict[str, Any]],
        normalized_input: dict[str, Any],
        estimated_input_tokens: int,
        effective_budget_tokens: int,
        compaction_trigger_tokens: int,
        message_sequence_through: int,
        estimator_revision: str,
        active_compaction_id: str | None = None,
    ) -> dict[str, Any]:
        if (
            estimated_input_tokens < 0
            or effective_budget_tokens < 1
            or compaction_trigger_tokens < 1
            or message_sequence_through < 1
        ):
            raise ValueError("Context assembly token and sequence values are invalid")
        self._validate_persisted_parts([{"type": "context", "value": normalized_input}])
        with self.lock, self.connection:
            run = self._one(
                """SELECT * FROM runs
                WHERE workspace_id = ? AND id = ? FOR UPDATE""",
                (workspace_id, run_id),
            )
            if run is None:
                raise RepositoryConflict("Run does not exist in this Workspace")
            input_message = self._one(
                """SELECT sequence FROM messages
                WHERE workspace_id = ? AND run_id = ? AND role = 'user'""",
                (workspace_id, run_id),
            )
            if (
                input_message is None
                or int(input_message["sequence"]) != message_sequence_through
            ):
                raise RepositoryConflict(
                    "Context assembly must end at this Run's immutable user message"
                )
            existing = self._one(
                """SELECT * FROM run_context_assemblies
                WHERE workspace_id = ? AND run_id = ?""",
                (workspace_id, run_id),
            )
            if existing is not None:
                materialized = self._context_assembly(existing)
                expected = {
                    "entries": entries,
                    "normalized_input": normalized_input,
                    "estimated_input_tokens": estimated_input_tokens,
                    "effective_budget_tokens": effective_budget_tokens,
                    "compaction_trigger_tokens": compaction_trigger_tokens,
                    "message_sequence_through": message_sequence_through,
                    "active_compaction_id": active_compaction_id,
                    "estimator_revision": estimator_revision,
                }
                if any(materialized[key] != value for key, value in expected.items()):
                    raise RepositoryConflict(
                        "Run already has a different immutable context assembly"
                    )
                return materialized
            if active_compaction_id:
                compaction = self._one(
                    """SELECT id FROM thread_compactions
                    WHERE workspace_id = ? AND thread_id = ? AND id = ?""",
                    (workspace_id, run["thread_id"], active_compaction_id),
                )
                if compaction is None:
                    raise RepositoryConflict(
                        "Active compaction does not belong to this Run Thread"
                    )
                compaction_surface = self._one(
                    """SELECT through_sequence FROM thread_compactions
                    WHERE workspace_id = ? AND thread_id = ? AND id = ?""",
                    (workspace_id, run["thread_id"], active_compaction_id),
                )
                if compaction_surface and int(
                    compaction_surface["through_sequence"]
                ) >= message_sequence_through:
                    raise RepositoryConflict(
                        "Active compaction cannot cover the current Run message"
                    )
            assembly_id, created_at = new_id("ctx"), utc_now()
            self.connection.execute(
                """INSERT INTO run_context_assemblies
                (id, workspace_id, thread_id, run_id, agent_version_id, entries_json,
                 normalized_input_json, estimated_input_tokens, effective_budget_tokens,
                 compaction_trigger_tokens, message_sequence_through, active_compaction_id,
                 estimator_revision, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    assembly_id,
                    workspace_id,
                    run["thread_id"],
                    run_id,
                    run["agent_version_id"],
                    self._json(entries),
                    self._json(normalized_input),
                    estimated_input_tokens,
                    effective_budget_tokens,
                    compaction_trigger_tokens,
                    message_sequence_through,
                    active_compaction_id,
                    estimator_revision,
                    created_at,
                ),
            )
            inserted = self._one(
                """SELECT * FROM run_context_assemblies
                WHERE workspace_id = ? AND id = ?""",
                (workspace_id, assembly_id),
            )
        return self._context_assembly(inserted) if inserted else {}

    def get_run_context_assembly(
        self,
        workspace_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        row = self._one(
            """SELECT * FROM run_context_assemblies
            WHERE workspace_id = ? AND run_id = ?""",
            (workspace_id, run_id),
        )
        return self._context_assembly(row) if row else None

    def get_run_customization_snapshot(
        self,
        workspace_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        row = self._one(
            """SELECT * FROM run_customization_snapshots
            WHERE workspace_id = ? AND run_id = ?""",
            (workspace_id, run_id),
        )
        if row is None:
            return None
        result = dict(row)
        result["snapshot"] = self._decoded_json(result.pop("snapshot_json"))
        return result

    def create_context_assembly(
        self,
        workspace_id: str,
        run_id: str,
        **payload: Any,
    ) -> dict[str, Any]:
        return self.save_run_context_assembly(workspace_id, run_id, **payload)

    def get_context_assembly(
        self,
        workspace_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        return self.get_run_context_assembly(workspace_id, run_id)

    def get_run(self, workspace_id: str, run_id: str) -> dict[str, Any] | None:
        row = self._one(
            """SELECT r.*,
            (SELECT m.id FROM messages m
             WHERE m.workspace_id = r.workspace_id AND m.run_id = r.id AND m.role = 'user'
             LIMIT 1) AS input_message_id,
            (SELECT m.id FROM messages m
             WHERE m.workspace_id = r.workspace_id AND m.run_id = r.id AND m.role = 'assistant'
             LIMIT 1) AS output_message_id,
            (SELECT c.id FROM run_context_assemblies c
             WHERE c.workspace_id = r.workspace_id AND c.run_id = r.id
             LIMIT 1) AS context_assembly_id
            FROM runs r WHERE r.workspace_id = ? AND r.id = ?""",
            (workspace_id, run_id),
        )
        return self._run(row) if row else None

    def list_runs(self, workspace_id: str, limit: int = 30) -> list[dict[str, Any]]:
        rows = self._all(
            """SELECT r.*, t.title, a.name AS agent_name,
            (SELECT m.id FROM messages m
             WHERE m.workspace_id = r.workspace_id AND m.run_id = r.id AND m.role = 'user'
             LIMIT 1) AS input_message_id,
            (SELECT m.id FROM messages m
             WHERE m.workspace_id = r.workspace_id AND m.run_id = r.id AND m.role = 'assistant'
             LIMIT 1) AS output_message_id,
            (SELECT c.id FROM run_context_assemblies c
             WHERE c.workspace_id = r.workspace_id AND c.run_id = r.id
             LIMIT 1) AS context_assembly_id
            FROM runs r JOIN threads t ON t.id = r.thread_id
            JOIN agent_versions v ON v.id = r.agent_version_id
            JOIN agents a ON a.id = v.agent_id
            WHERE r.workspace_id = ? ORDER BY r.created_at DESC LIMIT ?""",
            (workspace_id, limit),
        )
        return [self._run(row) for row in rows]

    def list_thread_runs(
        self,
        workspace_id: str,
        thread_id: str,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be positive")
        rows = self._all(
            """SELECT r.*, t.title, a.name AS agent_name,
            (SELECT m.id FROM messages m
             WHERE m.workspace_id = r.workspace_id AND m.run_id = r.id AND m.role = 'user'
             LIMIT 1) AS input_message_id,
            (SELECT m.id FROM messages m
             WHERE m.workspace_id = r.workspace_id AND m.run_id = r.id AND m.role = 'assistant'
             LIMIT 1) AS output_message_id,
            (SELECT c.id FROM run_context_assemblies c
             WHERE c.workspace_id = r.workspace_id AND c.run_id = r.id
             LIMIT 1) AS context_assembly_id
            FROM runs r JOIN threads t
              ON t.workspace_id = r.workspace_id AND t.id = r.thread_id
            JOIN agent_versions v
              ON v.workspace_id = r.workspace_id AND v.id = r.agent_version_id
            JOIN agents a
              ON a.workspace_id = r.workspace_id AND a.id = v.agent_id
            WHERE r.workspace_id = ? AND r.thread_id = ?
            ORDER BY r.created_at DESC LIMIT ?""",
            (workspace_id, thread_id, limit),
        )
        return [self._run(row) for row in rows]

    def recover_interrupted_runs(self) -> list[dict[str, Any]]:
        """Fail Runs whose in-memory worker disappeared during an API restart.

        Recovery is intentionally single-instance and does not retry provider or tool work. The
        complete recovery set is committed in one transaction so a Run cannot expose a failed
        status without its optional visible assistant message and terminal event.
        """
        recovered_at = utc_now()
        recovered: list[dict[str, Any]] = []
        failure_message = (
            "The API restarted before this Run finished. It was not retried automatically. "
            "Any external tool result from the interrupted Run may require verification."
        )
        with self.lock, self.connection:
            interrupted = self._all(
                """SELECT * FROM runs
                WHERE status IN ('queued', 'running')
                ORDER BY created_at, id FOR UPDATE"""
            )
            for run in interrupted:
                workspace_id = str(run["workspace_id"])
                run_id = str(run["id"])
                thread_id = str(run["thread_id"])
                delta_rows = self._all(
                    """SELECT payload_json FROM events
                    WHERE workspace_id = ? AND run_id = ? AND type = 'message.delta'
                    ORDER BY sequence""",
                    (workspace_id, run_id),
                )
                visible_fragments: list[str] = []
                for delta_row in delta_rows:
                    payload = self._decoded_json(delta_row["payload_json"])
                    delta = payload.get("delta") if isinstance(payload, Mapping) else None
                    if isinstance(delta, str):
                        visible_fragments.append(delta)
                visible_content = "".join(visible_fragments)

                output_message_id: str | None = None
                if visible_content.strip():
                    parts = [{"type": "text", "text": visible_content}]
                    self._validate_persisted_parts(parts)
                    sequence_row = self._one(
                        """UPDATE threads
                        SET next_message_sequence = next_message_sequence + 1, updated_at = ?
                        WHERE workspace_id = ? AND id = ?
                        RETURNING next_message_sequence AS value""",
                        (recovered_at, workspace_id, thread_id),
                    )
                    if sequence_row is None:
                        raise RepositoryConflict(
                            "Interrupted Run Thread does not exist in its Workspace"
                        )
                    output_message_id = new_id("msg")
                    self.connection.execute(
                        """INSERT INTO messages
                        (id, workspace_id, thread_id, run_id, agent_version_id, sequence, role,
                         status, parts_json, estimated_tokens, created_at, completed_at)
                        VALUES (?, ?, ?, ?, ?, ?, 'assistant', 'failed', ?, ?, ?, ?)""",
                        (
                            output_message_id,
                            workspace_id,
                            thread_id,
                            run_id,
                            run["agent_version_id"],
                            int(sequence_row["value"]),
                            self._json(parts),
                            self._estimate_tokens(visible_content),
                            recovered_at,
                            recovered_at,
                        ),
                    )

                existing_failure = self._one(
                    """SELECT id FROM events
                    WHERE workspace_id = ? AND run_id = ? AND type = 'run.failed'
                    ORDER BY sequence LIMIT 1""",
                    (workspace_id, run_id),
                )
                terminal_event_id: str
                if existing_failure is None:
                    sequence_row = self._one(
                        """UPDATE runs
                        SET status = 'failed', completed_at = ?,
                            next_event_sequence = next_event_sequence + 1
                        WHERE workspace_id = ? AND id = ?
                        RETURNING next_event_sequence AS value""",
                        (recovered_at, workspace_id, run_id),
                    )
                    if sequence_row is None:
                        raise RepositoryConflict(
                            "Interrupted Run disappeared during startup recovery"
                        )
                    terminal_event_id = new_id("evt")
                    context = self._one(
                        """SELECT id FROM run_context_assemblies
                        WHERE workspace_id = ? AND run_id = ?""",
                        (workspace_id, run_id),
                    )
                    payload = {
                        "code": "runtime_interrupted",
                        "message": failure_message,
                        "automatic_retry": False,
                        "external_tool_results": "verification_required",
                        "output_message_id": output_message_id,
                        "context_assembly_id": context["id"] if context else None,
                    }
                    self.connection.execute(
                        """INSERT INTO events
                        (id, workspace_id, run_id, sequence, type, payload_json, created_at)
                        VALUES (?, ?, ?, ?, 'run.failed', ?, ?)""",
                        (
                            terminal_event_id,
                            workspace_id,
                            run_id,
                            int(sequence_row["value"]),
                            self._json(payload),
                            recovered_at,
                        ),
                    )
                else:
                    terminal_event_id = str(existing_failure["id"])
                    self.connection.execute(
                        """UPDATE runs SET status = 'failed', completed_at = ?
                        WHERE workspace_id = ? AND id = ?""",
                        (recovered_at, workspace_id, run_id),
                    )

                recovered.append(
                    {
                        "workspace_id": workspace_id,
                        "thread_id": thread_id,
                        "run_id": run_id,
                        "status": "failed",
                        "output_message_id": output_message_id,
                        "terminal_event_id": terminal_event_id,
                    }
                )
        return recovered

    def set_run_status(self, workspace_id: str, run_id: str, status: str) -> None:
        terminal = status in {"completed", "failed", "cancelled"}
        with self.lock, self.connection:
            self.connection.execute(
                "UPDATE runs SET status = ?, completed_at = ? WHERE id = ? AND workspace_id = ?",
                (status, utc_now() if terminal else None, run_id, workspace_id),
            )

    def append_event(
        self, workspace_id: str, run_id: str, event_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        with self.lock, self.connection:
            row = self._one(
                """UPDATE runs
                SET next_event_sequence = next_event_sequence + 1
                WHERE workspace_id = ? AND id = ?
                RETURNING next_event_sequence AS value""",
                (workspace_id, run_id),
            )
            if row is None:
                raise RepositoryConflict("Run does not exist in this Workspace")
            sequence = int(row["value"])
            event_id, created_at = new_id("evt"), utc_now()
            self.connection.execute(
                """INSERT INTO events
                (id, workspace_id, run_id, sequence, type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    workspace_id,
                    run_id,
                    sequence,
                    event_type,
                    self._json(payload),
                    created_at,
                ),
            )
        return {
            "id": event_id,
            "run_id": run_id,
            "sequence": sequence,
            "type": event_type,
            "timestamp": created_at,
            "payload": payload,
        }

    def list_events(
        self, workspace_id: str, run_id: str, after: int = 0
    ) -> list[dict[str, Any]]:
        rows = self._all(
            """SELECT * FROM events
            WHERE workspace_id = ? AND run_id = ? AND sequence > ? ORDER BY sequence""",
            (workspace_id, run_id, after),
        )
        return [
            {
                "id": row["id"],
                "run_id": row["run_id"],
                "sequence": row["sequence"],
                "type": row["type"],
                "timestamp": row["created_at"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def list_thread_citation_events(
        self, workspace_id: str, thread_id: str, run_ids: list[str]
    ) -> list[dict[str, Any]]:
        """One bounded projection query; never expand all of a Thread's traces."""
        selected_ids = list(dict.fromkeys(run_ids))
        if len(selected_ids) > 50:
            raise ValueError("At most 50 Runs may be projected per Thread page")
        if not selected_ids:
            return []
        placeholders = ",".join("?" for _ in selected_ids)
        rows = self._all(
            f"""SELECT * FROM (
                SELECT e.*, ROW_NUMBER() OVER (
                    PARTITION BY e.run_id ORDER BY e.sequence
                ) AS citation_rank
                FROM events e JOIN runs r
                  ON r.id = e.run_id AND r.workspace_id = e.workspace_id
                WHERE e.workspace_id = ? AND r.thread_id = ?
                  AND e.type = 'citation.created' AND e.run_id IN ({placeholders})
            ) selected WHERE citation_rank <= 128 ORDER BY run_id, sequence""",
            (workspace_id, thread_id, *selected_ids),
        )
        return [self._citation_event_projection(row) for row in rows]

    def list_run_citation_events(
        self, workspace_id: str, run_id: str
    ) -> list[dict[str, Any]]:
        rows = self._all(
            """SELECT e.* FROM events e JOIN runs r
                ON r.id = e.run_id AND r.workspace_id = e.workspace_id
                WHERE e.workspace_id = ? AND e.run_id = ? AND e.type = 'citation.created'
                ORDER BY e.sequence LIMIT 128""",
            (workspace_id, run_id),
        )
        return [self._citation_event_projection(row) for row in rows]

    @staticmethod
    def _citation_event_projection(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "run_id": row["run_id"],
            "sequence": row["sequence"],
            "type": row["type"],
            "timestamp": row["created_at"],
            "payload": json.loads(row["payload_json"]),
        }

    def create_approval(
        self, workspace_id: str, run_id: str, request: dict[str, Any]
    ) -> dict[str, Any]:
        approval_id, created_at = new_id("apr"), utc_now()
        with self.lock, self.connection:
            self.connection.execute(
                """INSERT INTO approvals
                (id, workspace_id, run_id, status, request_json, created_at)
                VALUES (?, ?, ?, 'pending', ?, ?)""",
                (approval_id, workspace_id, run_id, self._json(request), created_at),
            )
        return {
            "id": approval_id,
            "workspace_id": workspace_id,
            "run_id": run_id,
            "status": "pending",
            "request": request,
            "created_at": created_at,
        }

    def get_approval(
        self, workspace_id: str, approval_id: str
    ) -> dict[str, Any] | None:
        row = self._one(
            "SELECT * FROM approvals WHERE workspace_id = ? AND id = ?",
            (workspace_id, approval_id),
        )
        if not row:
            return None
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json"))
        return result

    def decide_approval(
        self, workspace_id: str, approval_id: str, decision: str, note: str | None
    ) -> dict[str, Any] | None:
        with self.lock, self.connection:
            cursor = self.connection.execute(
                """UPDATE approvals SET status = ?, note = ?, decided_at = ?
                WHERE workspace_id = ? AND id = ? AND status = 'pending'""",
                (decision, note, utc_now(), workspace_id, approval_id),
            )
        return self.get_approval(workspace_id, approval_id) if cursor.rowcount else None

    def decide_task_approval(
        self,
        workspace_id: str,
        approval_id: str,
        decision: str,
        note: str | None,
    ) -> dict[str, Any] | None:
        """Atomically decide a linked approval and cross the Task approval boundary.

        Locking the Approval and owning Task in one transaction gives pause/cancel and
        approval a deterministic winner. If approval wins, the Task is already back in
        ``running`` before any external mutation can begin; a later pause/cancel request
        therefore applies to that in-flight boundary instead of revoking authorization
        after the tool has started.
        """
        if decision not in {"approved", "denied"}:
            raise ValueError("Approval decision must be approved or denied")
        decided_at = utc_now()
        with self.lock, self.connection:
            approval = self._one(
                """SELECT * FROM approvals
                WHERE workspace_id = ? AND id = ? FOR UPDATE""",
                (workspace_id, approval_id),
            )
            if approval is None or approval["status"] != "pending":
                return None
            link = self._one(
                """SELECT * FROM task_run_links
                WHERE workspace_id = ? AND run_id = ?""",
                (workspace_id, approval["run_id"]),
            )
            if link is None:
                raise RepositoryConflict("Approval is not linked to a Task Run")
            task = self._get_task_locked(
                workspace_id,
                str(link["task_id"]),
                for_update=True,
            )
            self._require_task_status(task, "decide approval for", {"waiting_for_approval"})
            if str(task["current_step_id"] or "") != str(link["step_id"]):
                raise RepositoryConflict("Approval no longer belongs to the active Task step")

            suspension_event = self._one(
                """SELECT payload_json FROM task_events
                WHERE workspace_id = ? AND task_id = ?
                  AND type = 'task.waiting_for_approval'
                ORDER BY sequence DESC LIMIT 1""",
                (workspace_id, task["id"]),
            )
            suspension_payload = (
                self._decoded_json(suspension_event["payload_json"])
                if suspension_event is not None
                else {}
            )
            if str(suspension_payload.get("reference_id") or "") != approval_id:
                raise RepositoryConflict("Approval is not the active Task suspension")

            self.connection.execute(
                """UPDATE approvals SET status = ?, note = ?, decided_at = ?
                WHERE workspace_id = ? AND id = ? AND status = 'pending'""",
                (decision, note, decided_at, workspace_id, approval_id),
            )
            step = self._active_task_step_locked(
                workspace_id,
                task,
                str(link["step_id"]),
            )
            attempt = self._latest_task_attempt_locked(
                workspace_id,
                str(task["id"]),
                str(step["id"]),
            )
            if step["status"] != "waiting_for_approval":
                raise RepositoryConflict("Task step is no longer waiting for approval")
            self.connection.execute(
                """UPDATE task_steps SET status = 'running'
                WHERE workspace_id = ? AND task_id = ? AND id = ?""",
                (workspace_id, task["id"], step["id"]),
            )
            attempt_id: str | None = None
            if attempt is not None and attempt["status"] == "waiting_for_approval":
                attempt_id = str(attempt["id"])
                self.connection.execute(
                    """UPDATE task_step_attempts SET status = 'running', updated_at = ?
                    WHERE workspace_id = ? AND task_id = ? AND id = ?""",
                    (decided_at, workspace_id, task["id"], attempt_id),
                )
            self._set_task_dispatch_locked(workspace_id, str(task["id"]), "blocked")
            updated, _event, _checkpoint = self._record_task_transition_locked(
                workspace_id,
                task,
                expected_revision=int(task["revision"]),
                event_type="task.resumed",
                payload={
                    "step_id": step["id"],
                    "approval_id": approval_id,
                    "decision": decision,
                },
                changes={"status": "running", "control_reason": None},
                checkpoint_step_id=str(step["id"]),
                checkpoint_attempt_id=attempt_id,
            )
            approval_result = dict(approval)
            approval_result["status"] = decision
            approval_result["note"] = note
            approval_result["decided_at"] = decided_at
            approval_result["request"] = self._decoded_json(
                approval_result.pop("request_json")
            )
            return {
                "approval": approval_result,
                "task": self._hydrate_task_locked(workspace_id, str(updated["id"])),
            }

    def list_extensions(self, workspace_id: str) -> list[dict[str, Any]]:
        rows = self._all(
            "SELECT * FROM extensions WHERE workspace_id = ? ORDER BY installed_at",
            (workspace_id,),
        )
        return [self._extension(row) for row in rows]

    @staticmethod
    def _extension(row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["manifest"] = json.loads(result.pop("manifest_json"))
        result["credential_refs"] = json.loads(result.pop("credential_refs_json"))
        return result

    def get_extension(
        self, workspace_id: str, extension_id: str
    ) -> dict[str, Any] | None:
        row = self._one(
            "SELECT * FROM extensions WHERE workspace_id = ? AND id = ?",
            (workspace_id, extension_id),
        )
        return self._extension(row) if row else None

    def install_extension(
        self,
        workspace_id: str,
        manifest: ExtensionManifest,
        credential_refs: dict[str, str],
    ) -> dict[str, Any]:
        extension_id = new_id("ext")
        with self.lock, self.connection:
            self.connection.execute(
                """INSERT INTO extensions
                (id, workspace_id, manifest_id, name, version, status, health, manifest_json,
                 credential_refs_json, installed_at)
                VALUES (?, ?, ?, ?, ?, 'disabled', 'unchecked', ?, ?, ?)
                ON CONFLICT(workspace_id, manifest_id) DO UPDATE SET
                  name = excluded.name,
                  version = excluded.version,
                  status = 'disabled',
                  health = 'unchecked',
                  manifest_json = excluded.manifest_json,
                  credential_refs_json = excluded.credential_refs_json""",
                (
                    extension_id,
                    workspace_id,
                    manifest.id,
                    manifest.name,
                    manifest.version,
                    manifest.model_dump_json(),
                    self._json(credential_refs),
                    utc_now(),
                ),
            )
            row = self._one(
                "SELECT * FROM extensions WHERE workspace_id = ? AND manifest_id = ?",
                (workspace_id, manifest.id),
            )
        return self._extension(row) if row else {}

    def update_extension(
        self,
        workspace_id: str,
        extension_id: str,
        *,
        status: str | None = None,
        health: str | None = None,
    ) -> dict[str, Any] | None:
        extension = self.get_extension(workspace_id, extension_id)
        if not extension:
            return None
        with self.lock, self.connection:
            self.connection.execute(
                "UPDATE extensions SET status = ?, health = ? WHERE workspace_id = ? AND id = ?",
                (
                    status or extension["status"],
                    health or extension["health"],
                    workspace_id,
                    extension_id,
                ),
            )
        return self.get_extension(workspace_id, extension_id)

    def update_extension_credentials(
        self,
        workspace_id: str,
        extension_id: str,
        credential_refs: dict[str, str],
    ) -> dict[str, Any] | None:
        with self.lock, self.connection:
            cursor = self.connection.execute(
                """UPDATE extensions
                SET credential_refs_json = ?, status = 'disabled', health = 'unchecked'
                WHERE workspace_id = ? AND id = ?""",
                (self._json(credential_refs), workspace_id, extension_id),
            )
        return (
            self.get_extension(workspace_id, extension_id) if cursor.rowcount else None
        )

    def update_extension_manifest(
        self,
        workspace_id: str,
        extension_id: str,
        manifest: ExtensionManifest,
    ) -> dict[str, Any] | None:
        with self.lock, self.connection:
            cursor = self.connection.execute(
                """UPDATE extensions SET manifest_json = ?, name = ?, version = ?
                WHERE workspace_id = ? AND id = ?""",
                (
                    manifest.model_dump_json(),
                    manifest.name,
                    manifest.version,
                    workspace_id,
                    extension_id,
                ),
            )
        return (
            self.get_extension(workspace_id, extension_id) if cursor.rowcount else None
        )

    def bootstrap(self, workspace_id: str) -> dict[str, Any] | None:
        workspace = self.workspace(workspace_id)
        if not workspace:
            return None
        return {
            "workspace": workspace,
            "agents": self.list_agents(workspace_id),
            "extensions": self.list_extensions(workspace_id),
            "knowledge_sources": self.list_knowledge_sources(workspace_id),
            "threads": self.list_threads(workspace_id),
            "runs": self.list_runs(workspace_id),
        }

    def create_knowledge_source(
        self,
        workspace_id: str,
        payload: KnowledgeSourceCreate,
    ) -> dict[str, Any]:
        source_id = new_id("ksrc")
        created_at = utc_now()
        try:
            with self.lock, self.connection:
                self.connection.execute(
                    """INSERT INTO knowledge_sources
                    (id, workspace_id, name, description, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'ready', ?, ?)""",
                    (
                        source_id,
                        workspace_id,
                        payload.name.strip(),
                        payload.description.strip(),
                        created_at,
                        created_at,
                    ),
                )
        except Exception as exc:
            if self._is_unique_violation(
                exc,
                postgres_constraint="knowledge_sources_workspace_id_name_key",
            ):
                raise RepositoryConflict(
                    "A knowledge source with this name already exists"
                ) from exc
            raise
        return self.get_knowledge_source(workspace_id, source_id) or {}

    def list_knowledge_sources(self, workspace_id: str) -> list[dict[str, Any]]:
        rows = self._all(
            """SELECT s.*,
                COUNT(d.id) AS document_count,
                COALESCE(SUM(CASE WHEN d.status = 'ready' THEN d.chunk_count ELSE 0 END), 0)
                    AS chunk_count
            FROM knowledge_sources s
            LEFT JOIN knowledge_documents d
                ON d.source_id = s.id AND d.workspace_id = s.workspace_id
            WHERE s.workspace_id = ?
            GROUP BY s.id
            ORDER BY s.created_at DESC""",
            (workspace_id,),
        )
        return [dict(row) for row in rows]

    def get_knowledge_source(
        self,
        workspace_id: str,
        source_id: str,
    ) -> dict[str, Any] | None:
        row = self._one(
            """SELECT s.*,
                COUNT(d.id) AS document_count,
                COALESCE(SUM(CASE WHEN d.status = 'ready' THEN d.chunk_count ELSE 0 END), 0)
                    AS chunk_count
            FROM knowledge_sources s
            LEFT JOIN knowledge_documents d
                ON d.source_id = s.id AND d.workspace_id = s.workspace_id
            WHERE s.workspace_id = ? AND s.id = ?
            GROUP BY s.id""",
            (workspace_id, source_id),
        )
        return dict(row) if row else None

    def begin_knowledge_document(
        self,
        workspace_id: str,
        source_id: str,
        *,
        title: str,
        source_uri: str | None,
        content: str,
        content_hash: str,
        index_revision: str,
        metadata: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        with self.lock, self.connection:
            existing = self._one(
                """SELECT * FROM knowledge_documents
                WHERE workspace_id = ? AND source_id = ? AND content_hash = ?""",
                (workspace_id, source_id, content_hash),
            )
            if (
                existing
                and existing["status"] == "ready"
                and existing["index_revision"] == index_revision
            ):
                return self._knowledge_document(existing), False

            updated_at = utc_now()
            if existing:
                self.connection.execute(
                    """UPDATE knowledge_documents
                    SET title = ?, source_uri = ?, content = ?, index_revision = ?,
                        metadata_json = ?, status = 'indexing', error = NULL, updated_at = ?
                    WHERE workspace_id = ? AND id = ?""",
                    (
                        title,
                        source_uri,
                        content,
                        index_revision,
                        self._json(metadata),
                        updated_at,
                        workspace_id,
                        existing["id"],
                    ),
                )
                row = self._one(
                    "SELECT * FROM knowledge_documents WHERE workspace_id = ? AND id = ?",
                    (workspace_id, existing["id"]),
                )
                return self._knowledge_document(row), True

            document_id = new_id("kdoc")
            self.connection.execute(
                """INSERT INTO knowledge_documents
                (id, workspace_id, source_id, title, source_uri, content, content_hash,
                 index_revision, chunk_count, status, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 'indexing', ?, ?, ?)""",
                (
                    document_id,
                    workspace_id,
                    source_id,
                    title,
                    source_uri,
                    content,
                    content_hash,
                    index_revision,
                    self._json(metadata),
                    updated_at,
                    updated_at,
                ),
            )
            row = self._one(
                "SELECT * FROM knowledge_documents WHERE workspace_id = ? AND id = ?",
                (workspace_id, document_id),
            )
            return self._knowledge_document(row), True

    def finish_knowledge_document(
        self,
        workspace_id: str,
        document_id: str,
        *,
        chunk_count: int,
    ) -> dict[str, Any] | None:
        with self.lock, self.connection:
            cursor = self.connection.execute(
                """UPDATE knowledge_documents
                SET status = 'ready', chunk_count = ?, error = NULL, updated_at = ?
                WHERE workspace_id = ? AND id = ?""",
                (chunk_count, utc_now(), workspace_id, document_id),
            )
        return (
            self.get_knowledge_document(workspace_id, document_id)
            if cursor.rowcount
            else None
        )

    def fail_knowledge_document(
        self,
        workspace_id: str,
        document_id: str,
        *,
        error: str,
    ) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                """UPDATE knowledge_documents
                SET status = 'failed', error = ?, updated_at = ?
                WHERE workspace_id = ? AND id = ?""",
                (error[:300], utc_now(), workspace_id, document_id),
            )

    @staticmethod
    def _knowledge_document(row: Mapping[str, Any] | None) -> dict[str, Any]:
        if row is None:
            return {}
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        result.pop("content", None)
        return result

    def get_knowledge_document(
        self,
        workspace_id: str,
        document_id: str,
    ) -> dict[str, Any] | None:
        row = self._one(
            "SELECT * FROM knowledge_documents WHERE workspace_id = ? AND id = ?",
            (workspace_id, document_id),
        )
        return self._knowledge_document(row) if row else None

    def list_knowledge_documents(
        self,
        workspace_id: str,
        source_id: str,
    ) -> list[dict[str, Any]]:
        rows = self._all(
            """SELECT * FROM knowledge_documents
            WHERE workspace_id = ? AND source_id = ? ORDER BY created_at DESC""",
            (workspace_id, source_id),
        )
        return [self._knowledge_document(row) for row in rows]

    def delete_knowledge_source(self, workspace_id: str, source_id: str) -> bool:
        with self.lock, self.connection:
            cursor = self.connection.execute(
                "DELETE FROM knowledge_sources WHERE workspace_id = ? AND id = ?",
                (workspace_id, source_id),
            )
        return bool(cursor.rowcount)

    def knowledge_source_references(
        self,
        workspace_id: str,
        source_id: str,
    ) -> list[dict[str, Any]]:
        rows = self._all(
            """SELECT v.id AS version_id, v.agent_id, v.version, v.definition_json,
                a.name AS agent_name
            FROM agent_versions v JOIN agents a ON a.id = v.agent_id
            WHERE v.workspace_id = ?""",
            (workspace_id,),
        )
        references: list[dict[str, Any]] = []
        for row in rows:
            definition = json.loads(row["definition_json"])
            if source_id not in definition.get("knowledge", []):
                continue
            item = dict(row)
            item.pop("definition_json", None)
            references.append(item)
        return references

    def _create_skill_locked(
        self,
        workspace_id: str,
        payload: SkillCreate,
    ) -> str:
        if payload.source_kind != "native" and payload.enabled:
            raise RepositoryConflict(
                "Imported Skills must be installed disabled before review"
            )
        skill_id, version_id, created_at = new_id("skl"), new_id("skv"), utc_now()
        self._validate_no_raw_secrets(payload.model_dump(mode="json"))
        definition_json, definition_sha256 = self._customization_snapshot(
            payload.definition
        )
        self.connection.execute(
            """INSERT INTO skills
            (id, workspace_id, slug, enabled, current_version_id, source_kind,
             source_ref, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                skill_id,
                workspace_id,
                payload.slug,
                payload.enabled,
                version_id,
                payload.source_kind,
                payload.source_ref,
                created_at,
                created_at,
            ),
        )
        self.connection.execute(
            """INSERT INTO skill_versions
            (id, workspace_id, skill_id, version, definition_json,
             definition_sha256, created_at)
            VALUES (?, ?, ?, 1, ?, ?, ?)""",
            (
                version_id,
                workspace_id,
                skill_id,
                definition_json,
                definition_sha256,
                created_at,
            ),
        )
        return skill_id

    def create_skill(
        self,
        workspace_id: str,
        payload: SkillCreate,
    ) -> dict[str, Any]:
        try:
            with self.lock, self.connection:
                skill_id = self._create_skill_locked(workspace_id, payload)
        except Exception as exc:
            if self._is_unique_violation(
                exc,
                postgres_constraint="skills_workspace_id_slug_key",
            ):
                raise RepositoryConflict(
                    "Skill slug already exists in this Workspace"
                ) from exc
            raise
        return self.get_skill(workspace_id, skill_id) or {}

    def list_skills(
        self,
        workspace_id: str,
        *,
        enabled_only: bool = False,
    ) -> list[dict[str, Any]]:
        query = """SELECT s.*, v.version, v.definition_json, v.definition_sha256,
            v.created_at AS version_created_at
            FROM skills s
            JOIN skill_versions v
              ON v.id = s.current_version_id AND v.workspace_id = s.workspace_id
            WHERE s.workspace_id = ?"""
        if enabled_only:
            query += " AND s.enabled = TRUE"
        query += " ORDER BY s.updated_at DESC, s.id"
        return [
            self._customization_identity(row)
            for row in self._all(query, (workspace_id,))
        ]

    def list_skill_summaries(
        self,
        workspace_id: str,
        *,
        enabled_only: bool = False,
        limit: int = _SUMMARY_DEFAULT_LIMIT,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        limit, offset = self._summary_page(limit=limit, offset=offset)
        query = """SELECT s.id, s.workspace_id, s.slug, s.enabled,
            s.current_version_id, v.definition_sha256, s.source_kind,
            s.created_at, s.updated_at,
            COALESCE(v.definition_json::jsonb ->> 'name', s.slug) AS name,
            COALESCE(
                NULLIF(v.definition_json::jsonb #>> '{metadata,display_name}', ''),
                v.definition_json::jsonb ->> 'name',
                s.slug
            ) AS display_name,
            COALESCE(v.definition_json::jsonb ->> 'description', '') AS description,
            COALESCE(
                (v.definition_json::jsonb ->> 'disable_model_invocation')::boolean,
                FALSE
            ) AS disable_model_invocation,
            COALESCE(
                (v.definition_json::jsonb ->> 'user_invocable')::boolean,
                TRUE
            ) AS user_invocable,
            COALESCE(
                v.definition_json::jsonb -> 'required_tools',
                '[]'::jsonb
            )::text AS required_tools_json,
            jsonb_array_length(
                COALESCE(
                    v.definition_json::jsonb -> 'resources',
                    '[]'::jsonb
                )
            ) AS resource_count
            FROM skills s
            JOIN skill_versions v
              ON v.id = s.current_version_id AND v.workspace_id = s.workspace_id
            WHERE s.workspace_id = ?"""
        params: list[Any] = [workspace_id]
        if enabled_only:
            query += " AND s.enabled = TRUE"
        query += " ORDER BY s.updated_at DESC, s.id LIMIT ? OFFSET ?"
        params.extend((limit, offset))
        return [
            self._skill_summary(row)
            for row in self._all(query, tuple(params))
        ]

    def get_skill(
        self,
        workspace_id: str,
        skill_id: str,
    ) -> dict[str, Any] | None:
        row = self._one(
            """SELECT s.*, v.version, v.definition_json, v.definition_sha256,
                v.created_at AS version_created_at
            FROM skills s
            JOIN skill_versions v
              ON v.id = s.current_version_id AND v.workspace_id = s.workspace_id
            WHERE s.workspace_id = ? AND s.id = ?""",
            (workspace_id, skill_id),
        )
        return self._customization_identity(row) if row else None

    def get_skill_version(
        self,
        workspace_id: str,
        skill_version_id: str,
    ) -> dict[str, Any] | None:
        row = self._one(
            """SELECT * FROM skill_versions
            WHERE workspace_id = ? AND id = ?""",
            (workspace_id, skill_version_id),
        )
        return self._customization_version(row) if row else None

    def list_skill_versions(
        self,
        workspace_id: str,
        skill_id: str,
    ) -> list[dict[str, Any]]:
        rows = self._all(
            """SELECT * FROM skill_versions
            WHERE workspace_id = ? AND skill_id = ? ORDER BY version DESC""",
            (workspace_id, skill_id),
        )
        return [self._customization_version(row) for row in rows]

    def create_skill_version(
        self,
        workspace_id: str,
        skill_id: str,
        payload: SkillVersionCreate,
    ) -> dict[str, Any] | None:
        self._validate_no_raw_secrets(payload.model_dump(mode="json"))
        version_id, created_at = new_id("skv"), utc_now()
        definition_json, definition_sha256 = self._customization_snapshot(
            payload.definition
        )
        with self.lock, self.connection:
            skill = self._one(
                """SELECT id FROM skills
                WHERE workspace_id = ? AND id = ? FOR UPDATE""",
                (workspace_id, skill_id),
            )
            if skill is None:
                return None
            latest = self._one(
                """SELECT COALESCE(MAX(version), 0) AS value FROM skill_versions
                WHERE workspace_id = ? AND skill_id = ?""",
                (workspace_id, skill_id),
            )
            version = int(latest["value"]) + 1 if latest else 1
            self.connection.execute(
                """INSERT INTO skill_versions
                (id, workspace_id, skill_id, version, definition_json,
                 definition_sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    version_id,
                    workspace_id,
                    skill_id,
                    version,
                    definition_json,
                    definition_sha256,
                    created_at,
                ),
            )
            self.connection.execute(
                """UPDATE skills SET current_version_id = ?, updated_at = ?
                WHERE workspace_id = ? AND id = ?""",
                (version_id, created_at, workspace_id, skill_id),
            )
        return self.get_skill(workspace_id, skill_id)

    def update_skill(
        self,
        workspace_id: str,
        skill_id: str,
        payload: SkillPatch,
    ) -> dict[str, Any] | None:
        with self.lock, self.connection:
            cursor = self.connection.execute(
                """UPDATE skills SET enabled = ?, updated_at = ?
                WHERE workspace_id = ? AND id = ?""",
                (payload.enabled, utc_now(), workspace_id, skill_id),
            )
        return self.get_skill(workspace_id, skill_id) if cursor.rowcount else None

    def list_bound_skill_versions(
        self,
        workspace_id: str,
        agent_version_id: str,
        *,
        enabled_only: bool = True,
    ) -> list[dict[str, Any]]:
        query = """SELECT sv.*, s.slug, s.enabled, b.agent_version_id,
            b.mode, b.position
            FROM agent_version_skill_bindings b
            JOIN agent_versions av
              ON av.id = b.agent_version_id AND av.workspace_id = b.workspace_id
            JOIN skill_versions sv
              ON sv.id = b.skill_version_id AND sv.workspace_id = b.workspace_id
            JOIN skills s
              ON s.id = sv.skill_id AND s.workspace_id = sv.workspace_id
            WHERE b.workspace_id = ? AND b.agent_version_id = ?"""
        if enabled_only:
            query += " AND s.enabled = TRUE"
        query += " ORDER BY b.position"
        return [
            self._customization_version(row)
            for row in self._all(query, (workspace_id, agent_version_id))
        ]

    def get_bound_skill_version(
        self,
        workspace_id: str,
        agent_version_id: str,
        skill_version_id: str,
    ) -> dict[str, Any] | None:
        row = self._one(
            """SELECT sv.*, s.slug, s.enabled, b.agent_version_id,
                b.mode, b.position
            FROM agent_version_skill_bindings b
            JOIN agent_versions av
              ON av.id = b.agent_version_id AND av.workspace_id = b.workspace_id
            JOIN skill_versions sv
              ON sv.id = b.skill_version_id AND sv.workspace_id = b.workspace_id
            JOIN skills s
              ON s.id = sv.skill_id AND s.workspace_id = sv.workspace_id
            WHERE b.workspace_id = ? AND b.agent_version_id = ?
              AND b.skill_version_id = ?""",
            (workspace_id, agent_version_id, skill_version_id),
        )
        return self._customization_version(row) if row else None

    def _create_rule_locked(
        self,
        workspace_id: str,
        payload: RuleCreate,
    ) -> str:
        if payload.source_kind != "native" and payload.enabled:
            raise RepositoryConflict(
                "Imported Rules must be installed disabled before review"
            )
        rule_id, version_id, created_at = new_id("rul"), new_id("ruv"), utc_now()
        self._validate_no_raw_secrets(payload.model_dump(mode="json"))
        definition_json, definition_sha256 = self._customization_snapshot(
            payload.definition
        )
        if payload.thread_id is not None:
            thread = self._one(
                """SELECT id FROM threads
                WHERE workspace_id = ? AND id = ?""",
                (workspace_id, payload.thread_id),
            )
            if thread is None:
                raise RepositoryConflict(
                    "Rule Thread does not belong to this Workspace"
                )
        self.connection.execute(
            """INSERT INTO rules
            (id, workspace_id, slug, scope, thread_id, enabled,
             current_version_id, source_kind, source_ref, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rule_id,
                workspace_id,
                payload.slug,
                payload.scope,
                payload.thread_id,
                payload.enabled,
                version_id,
                payload.source_kind,
                payload.source_ref,
                created_at,
                created_at,
            ),
        )
        self.connection.execute(
            """INSERT INTO rule_versions
            (id, workspace_id, rule_id, version, definition_json,
             definition_sha256, created_at)
            VALUES (?, ?, ?, 1, ?, ?, ?)""",
            (
                version_id,
                workspace_id,
                rule_id,
                definition_json,
                definition_sha256,
                created_at,
            ),
        )
        return rule_id

    def create_rule(
        self,
        workspace_id: str,
        payload: RuleCreate,
    ) -> dict[str, Any]:
        try:
            with self.lock, self.connection:
                rule_id = self._create_rule_locked(workspace_id, payload)
        except Exception as exc:
            if self._is_unique_violation(
                exc,
                postgres_constraint="rules_workspace_id_slug_key",
            ):
                raise RepositoryConflict(
                    "Rule slug already exists in this Workspace"
                ) from exc
            raise
        return self.get_rule(workspace_id, rule_id) or {}

    def list_rules(
        self,
        workspace_id: str,
        *,
        enabled_only: bool = False,
        scope: str | None = None,
        thread_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query = """SELECT r.*, v.version, v.definition_json, v.definition_sha256,
            v.created_at AS version_created_at
            FROM rules r
            JOIN rule_versions v
              ON v.id = r.current_version_id AND v.workspace_id = r.workspace_id
            WHERE r.workspace_id = ?"""
        params: list[Any] = [workspace_id]
        if enabled_only:
            query += " AND r.enabled = TRUE"
        if scope is not None:
            query += " AND r.scope = ?"
            params.append(scope)
        if thread_id is not None:
            query += " AND (r.scope <> 'thread' OR r.thread_id = ?)"
            params.append(thread_id)
        query += " ORDER BY r.updated_at DESC, r.id"
        return [
            self._customization_identity(row)
            for row in self._all(query, tuple(params))
        ]

    def list_rule_summaries(
        self,
        workspace_id: str,
        *,
        enabled_only: bool = False,
        scope: str | None = None,
        thread_id: str | None = None,
        limit: int = _SUMMARY_DEFAULT_LIMIT,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        limit, offset = self._summary_page(limit=limit, offset=offset)
        query = """SELECT r.id, r.workspace_id, r.slug, r.scope, r.thread_id,
            r.enabled, r.current_version_id, v.definition_sha256, r.source_kind,
            r.created_at, r.updated_at,
            COALESCE(v.definition_json::jsonb ->> 'name', r.slug) AS name,
            COALESCE(v.definition_json::jsonb ->> 'description', '') AS description,
            COALESCE(v.definition_json::jsonb ->> 'activation', 'always') AS activation,
            COALESCE((v.definition_json::jsonb ->> 'priority')::integer, 100) AS priority,
            jsonb_array_length(
                COALESCE(
                    v.definition_json::jsonb #> '{conditions,prompt_terms}',
                    '[]'::jsonb
                )
            ) + jsonb_array_length(
                COALESCE(
                    v.definition_json::jsonb #> '{conditions,context_paths}',
                    '[]'::jsonb
                )
            ) + jsonb_array_length(
                COALESCE(
                    v.definition_json::jsonb #> '{conditions,file_globs}',
                    '[]'::jsonb
                )
            ) AS condition_count
            FROM rules r
            JOIN rule_versions v
              ON v.id = r.current_version_id AND v.workspace_id = r.workspace_id
            WHERE r.workspace_id = ?"""
        params: list[Any] = [workspace_id]
        if enabled_only:
            query += " AND r.enabled = TRUE"
        if scope is not None:
            query += " AND r.scope = ?"
            params.append(scope)
        if thread_id is not None:
            query += " AND (r.scope <> 'thread' OR r.thread_id = ?)"
            params.append(thread_id)
        query += " ORDER BY r.updated_at DESC, r.id LIMIT ? OFFSET ?"
        params.extend((limit, offset))
        return [
            self._rule_summary(row)
            for row in self._all(query, tuple(params))
        ]

    def get_rule(
        self,
        workspace_id: str,
        rule_id: str,
    ) -> dict[str, Any] | None:
        row = self._one(
            """SELECT r.*, v.version, v.definition_json, v.definition_sha256,
                v.created_at AS version_created_at
            FROM rules r
            JOIN rule_versions v
              ON v.id = r.current_version_id AND v.workspace_id = r.workspace_id
            WHERE r.workspace_id = ? AND r.id = ?""",
            (workspace_id, rule_id),
        )
        return self._customization_identity(row) if row else None

    def get_rule_version(
        self,
        workspace_id: str,
        rule_version_id: str,
    ) -> dict[str, Any] | None:
        row = self._one(
            """SELECT * FROM rule_versions
            WHERE workspace_id = ? AND id = ?""",
            (workspace_id, rule_version_id),
        )
        return self._customization_version(row) if row else None

    def list_rule_versions(
        self,
        workspace_id: str,
        rule_id: str,
    ) -> list[dict[str, Any]]:
        rows = self._all(
            """SELECT * FROM rule_versions
            WHERE workspace_id = ? AND rule_id = ? ORDER BY version DESC""",
            (workspace_id, rule_id),
        )
        return [self._customization_version(row) for row in rows]

    def create_rule_version(
        self,
        workspace_id: str,
        rule_id: str,
        payload: RuleVersionCreate,
    ) -> dict[str, Any] | None:
        self._validate_no_raw_secrets(payload.model_dump(mode="json"))
        version_id, created_at = new_id("ruv"), utc_now()
        definition_json, definition_sha256 = self._customization_snapshot(
            payload.definition
        )
        with self.lock, self.connection:
            rule = self._one(
                """SELECT id FROM rules
                WHERE workspace_id = ? AND id = ? FOR UPDATE""",
                (workspace_id, rule_id),
            )
            if rule is None:
                return None
            latest = self._one(
                """SELECT COALESCE(MAX(version), 0) AS value FROM rule_versions
                WHERE workspace_id = ? AND rule_id = ?""",
                (workspace_id, rule_id),
            )
            version = int(latest["value"]) + 1 if latest else 1
            self.connection.execute(
                """INSERT INTO rule_versions
                (id, workspace_id, rule_id, version, definition_json,
                 definition_sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    version_id,
                    workspace_id,
                    rule_id,
                    version,
                    definition_json,
                    definition_sha256,
                    created_at,
                ),
            )
            self.connection.execute(
                """UPDATE rules SET current_version_id = ?, updated_at = ?
                WHERE workspace_id = ? AND id = ?""",
                (version_id, created_at, workspace_id, rule_id),
            )
        return self.get_rule(workspace_id, rule_id)

    def update_rule(
        self,
        workspace_id: str,
        rule_id: str,
        payload: RulePatch,
    ) -> dict[str, Any] | None:
        with self.lock, self.connection:
            cursor = self.connection.execute(
                """UPDATE rules SET enabled = ?, updated_at = ?
                WHERE workspace_id = ? AND id = ?""",
                (payload.enabled, utc_now(), workspace_id, rule_id),
            )
        return self.get_rule(workspace_id, rule_id) if cursor.rowcount else None

    def install_customization_bundle(
        self,
        workspace_id: str,
        *,
        skills: list[SkillCreate],
        rules: list[RuleCreate],
    ) -> dict[str, list[dict[str, Any]]]:
        """Atomically install an inspected plugin bundle in a disabled state."""
        skill_slugs = [payload.slug for payload in skills]
        rule_slugs = [payload.slug for payload in rules]
        if len(skill_slugs) != len(set(skill_slugs)):
            raise RepositoryConflict("Plugin bundle contains duplicate Skill slugs")
        if len(rule_slugs) != len(set(rule_slugs)):
            raise RepositoryConflict("Plugin bundle contains duplicate Rule slugs")
        if any(payload.enabled for payload in (*skills, *rules)):
            raise RepositoryConflict(
                "Plugin customization bundles must be installed disabled"
            )
        for payload in (*skills, *rules):
            self._validate_no_raw_secrets(payload.model_dump(mode="json"))

        try:
            with self.lock, self.connection:
                workspace = self._one(
                    "SELECT id FROM workspaces WHERE id = ? FOR UPDATE",
                    (workspace_id,),
                )
                if workspace is None:
                    raise RepositoryConflict("Workspace does not exist")
                for slug in skill_slugs:
                    if self._one(
                        "SELECT id FROM skills WHERE workspace_id = ? AND slug = ?",
                        (workspace_id, slug),
                    ):
                        raise RepositoryConflict(
                            "Skill slug already exists in this Workspace"
                        )
                for slug in rule_slugs:
                    if self._one(
                        "SELECT id FROM rules WHERE workspace_id = ? AND slug = ?",
                        (workspace_id, slug),
                    ):
                        raise RepositoryConflict(
                            "Rule slug already exists in this Workspace"
                        )
                for payload in rules:
                    if payload.thread_id is not None and self._one(
                        """SELECT id FROM threads
                        WHERE workspace_id = ? AND id = ?""",
                        (workspace_id, payload.thread_id),
                    ) is None:
                        raise RepositoryConflict(
                            "Rule Thread does not belong to this Workspace"
                        )
                skill_ids = [
                    self._create_skill_locked(workspace_id, payload)
                    for payload in skills
                ]
                rule_ids = [
                    self._create_rule_locked(workspace_id, payload)
                    for payload in rules
                ]
        except Exception as exc:
            constraint = getattr(getattr(exc, "diag", None), "constraint_name", None)
            if constraint == "skills_workspace_id_slug_key":
                raise RepositoryConflict(
                    "Skill slug already exists in this Workspace"
                ) from exc
            if constraint == "rules_workspace_id_slug_key":
                raise RepositoryConflict(
                    "Rule slug already exists in this Workspace"
                ) from exc
            raise
        return {
            "skills": [
                record
                for skill_id in skill_ids
                if (record := self.get_skill(workspace_id, skill_id)) is not None
            ],
            "rules": [
                record
                for rule_id in rule_ids
                if (record := self.get_rule(workspace_id, rule_id)) is not None
            ],
        }

    def _thread_configuration_locked(
        self,
        workspace_id: str,
        thread_id: str,
        *,
        for_update: bool = False,
        for_share: bool = False,
    ) -> Mapping[str, Any] | None:
        if for_update and for_share:
            raise ValueError("A Thread configuration cannot request two lock modes")
        suffix = " FOR UPDATE" if for_update else " FOR SHARE" if for_share else ""
        return self._one(
            """SELECT c.*, t.agent_version_id
            FROM thread_configurations c
            JOIN threads t
              ON t.id = c.thread_id AND t.workspace_id = c.workspace_id
            WHERE c.workspace_id = ? AND c.thread_id = ?"""
            + suffix,
            (workspace_id, thread_id),
        )

    def get_thread_configuration(
        self,
        workspace_id: str,
        thread_id: str,
    ) -> dict[str, Any] | None:
        with self.lock, self.connection:
            row = self._thread_configuration_locked(
                workspace_id,
                thread_id,
                for_share=True,
            )
            if row is None:
                return None
            skills = self._all(
                """SELECT skill_version_id FROM thread_configuration_skills
                WHERE workspace_id = ? AND thread_id = ? ORDER BY position""",
                (workspace_id, thread_id),
            )
            rules = self._all(
                """SELECT rule_version_id FROM thread_configuration_rules
                WHERE workspace_id = ? AND thread_id = ? ORDER BY position""",
                (workspace_id, thread_id),
            )
        result = dict(row)
        result["active_skill_version_ids"] = [
            item["skill_version_id"] for item in skills
        ]
        result["manual_rule_version_ids"] = [
            item["rule_version_id"] for item in rules
        ]
        return result

    def _validate_thread_configuration_refs_locked(
        self,
        workspace_id: str,
        thread_id: str,
        agent_version_id: str,
        payload: ThreadConfigurationUpdate,
    ) -> None:
        for skill_version_id in payload.active_skill_version_ids:
            bound = self._one(
                """SELECT b.skill_version_id, sv.definition_json
                FROM agent_version_skill_bindings b
                JOIN skills s
                  ON s.workspace_id = b.workspace_id
                JOIN skill_versions sv
                  ON sv.id = b.skill_version_id
                 AND sv.workspace_id = b.workspace_id
                 AND sv.skill_id = s.id
                WHERE b.workspace_id = ? AND b.agent_version_id = ?
                  AND b.skill_version_id = ? AND s.enabled = TRUE""",
                (workspace_id, agent_version_id, skill_version_id),
            )
            if bound is None:
                raise RepositoryConflict(
                    "Active Skill must be an enabled exact version bound to this Thread Agent version"
                )
            skill_definition = self._decoded_json(bound["definition_json"])
            if skill_definition.get("user_invocable", True) is not True:
                raise RepositoryConflict(
                    "Active Skill must allow explicit user invocation"
                )
        for rule_version_id in payload.manual_rule_version_ids:
            rule = self._one(
                """SELECT rv.definition_json, r.scope, r.thread_id, r.enabled,
                    EXISTS(
                        SELECT 1 FROM agent_version_rule_bindings b
                        WHERE b.workspace_id = rv.workspace_id
                          AND b.agent_version_id = ?
                          AND b.rule_version_id = rv.id
                    ) AS agent_bound
                FROM rule_versions rv
                JOIN rules r
                  ON r.id = rv.rule_id AND r.workspace_id = rv.workspace_id
                WHERE rv.workspace_id = ? AND rv.id = ?""",
                (agent_version_id, workspace_id, rule_version_id),
            )
            if rule is None or not rule["enabled"]:
                raise RepositoryConflict(
                    "Manual Rule must be an enabled exact version in this Workspace"
                )
            definition = self._decoded_json(rule["definition_json"])
            if definition.get("activation") != "manual":
                raise RepositoryConflict(
                    "Only manually activated Rules may be selected for the next turn"
                )
            thread_scoped = (
                rule["scope"] == "thread" and rule["thread_id"] == thread_id
            )
            workspace_scoped = rule["scope"] == "workspace"
            if not rule["agent_bound"] and not thread_scoped and not workspace_scoped:
                raise RepositoryConflict(
                    "Manual Rule must be Agent-bound or scoped to this Workspace or Thread"
                )

    def update_thread_configuration(
        self,
        workspace_id: str,
        thread_id: str,
        payload: ThreadConfigurationUpdate,
    ) -> dict[str, Any] | None:
        updated_at = utc_now()
        with self.lock, self.connection:
            current = self._thread_configuration_locked(
                workspace_id,
                thread_id,
                for_update=True,
            )
            if current is None:
                return None
            if int(current["revision"]) != payload.expected_revision:
                raise RepositoryConflict(
                    "Thread configuration changed; reload before saving"
                )
            agent_version_id = str(current["agent_version_id"])
            self._validate_thread_configuration_refs_locked(
                workspace_id,
                thread_id,
                agent_version_id,
                payload,
            )
            self.connection.execute(
                """DELETE FROM thread_configuration_skills
                WHERE workspace_id = ? AND thread_id = ?""",
                (workspace_id, thread_id),
            )
            self.connection.execute(
                """DELETE FROM thread_configuration_rules
                WHERE workspace_id = ? AND thread_id = ?""",
                (workspace_id, thread_id),
            )
            for position, skill_version_id in enumerate(
                payload.active_skill_version_ids
            ):
                self.connection.execute(
                    """INSERT INTO thread_configuration_skills
                    (thread_id, workspace_id, skill_version_id, position)
                    VALUES (?, ?, ?, ?)""",
                    (thread_id, workspace_id, skill_version_id, position),
                )
            for position, rule_version_id in enumerate(
                payload.manual_rule_version_ids
            ):
                self.connection.execute(
                    """INSERT INTO thread_configuration_rules
                    (thread_id, workspace_id, rule_version_id, position)
                    VALUES (?, ?, ?, ?)""",
                    (thread_id, workspace_id, rule_version_id, position),
                )
            changed = self.connection.execute(
                """UPDATE thread_configurations
                SET revision = revision + 1, updated_at = ?
                WHERE workspace_id = ? AND thread_id = ? AND revision = ?""",
                (updated_at, workspace_id, thread_id, payload.expected_revision),
            )
            if not changed.rowcount:
                raise RepositoryConflict(
                    "Thread configuration changed; reload before saving"
                )
        return self.get_thread_configuration(workspace_id, thread_id)

    @staticmethod
    def _empty_digest() -> str:
        return hashlib.sha256(b"").hexdigest()

    def get_workspace_preferences(
        self,
        workspace_id: str,
    ) -> dict[str, Any] | None:
        row = self._one(
            """SELECT w.id AS workspace_id,
                COALESCE(p.revision, 0) AS revision,
                COALESCE(p.content, '') AS content,
                COALESCE(p.content_sha256, ?) AS content_sha256,
                COALESCE(p.created_at, w.created_at) AS created_at,
                COALESCE(p.updated_at, w.created_at) AS updated_at
            FROM workspaces w
            LEFT JOIN workspace_preferences p ON p.workspace_id = w.id
            WHERE w.id = ?""",
            (self._empty_digest(), workspace_id),
        )
        return dict(row) if row else None

    def update_workspace_preferences(
        self,
        workspace_id: str,
        payload: PreferenceUpdate,
    ) -> dict[str, Any] | None:
        self._validate_no_raw_secrets(payload.content)
        content_sha256 = hashlib.sha256(payload.content.encode("utf-8")).hexdigest()
        updated_at = utc_now()
        with self.lock, self.connection:
            workspace = self._one(
                "SELECT id FROM workspaces WHERE id = ? FOR UPDATE",
                (workspace_id,),
            )
            if workspace is None:
                return None
            current = self._one(
                """SELECT revision FROM workspace_preferences
                WHERE workspace_id = ? FOR UPDATE""",
                (workspace_id,),
            )
            current_revision = int(current["revision"]) if current else 0
            if current_revision != payload.expected_revision:
                raise RepositoryConflict(
                    "Workspace preferences changed; reload before saving"
                )
            if current is None:
                self.connection.execute(
                    """INSERT INTO workspace_preferences
                    (workspace_id, revision, content, content_sha256, created_at, updated_at)
                    VALUES (?, 1, ?, ?, ?, ?)""",
                    (
                        workspace_id,
                        payload.content,
                        content_sha256,
                        updated_at,
                        updated_at,
                    ),
                )
            else:
                changed = self.connection.execute(
                    """UPDATE workspace_preferences
                    SET revision = revision + 1, content = ?, content_sha256 = ?,
                        updated_at = ?
                    WHERE workspace_id = ? AND revision = ?""",
                    (
                        payload.content,
                        content_sha256,
                        updated_at,
                        workspace_id,
                        payload.expected_revision,
                    ),
                )
                if not changed.rowcount:
                    raise RepositoryConflict(
                        "Workspace preferences changed; reload before saving"
                    )
        return self.get_workspace_preferences(workspace_id)

    # Durable Task runtime -------------------------------------------------

    _TASK_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
    _TASK_NONTERMINAL_STATUSES = frozenset({
        "draft",
        "planning",
        "ready",
        "running",
        "pause_requested",
        "paused",
        "waiting_for_approval",
        "waiting_for_user",
        "cancel_requested",
    })
    _TASK_STEP_TERMINAL_STATUSES = frozenset(
        {"completed", "failed", "skipped", "cancelled"}
    )
    _TASK_ATTEMPT_TERMINAL_STATUSES = frozenset({
        "completed",
        "failed",
        "interrupted",
        "cancelled",
    })

    @classmethod
    def _task_record(cls, row: Mapping[str, Any]) -> dict[str, Any]:
        decoded_error = cls._decoded_json(row.get("error_json"))
        if isinstance(decoded_error, Mapping):
            error = str(decoded_error.get("message") or cls._json(dict(decoded_error)))
        elif decoded_error is None:
            error = None
        else:
            error = str(decoded_error)
        return {
            "id": row["id"],
            "workspace_id": row["workspace_id"],
            "thread_id": row["thread_id"],
            "goal": row["goal"],
            "model_override": row.get("model_override"),
            "reasoning_effort": row.get("reasoning_effort"),
            "status": row["status"],
            "current_step_id": row["current_step_id"],
            "result": cls._decoded_json(row.get("result_json")),
            "revision": int(row["revision"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
            "error": error,
        }

    @classmethod
    def _task_step_record(cls, row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "task_id": row["task_id"],
            "ordinal": int(row["position"]),
            "title": row["title"],
            "description": row["description"],
            "status": row["status"],
            "attempts": [],
            "evidence": cls._decoded_json(row.get("evidence_json") or "[]"),
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
        }

    @classmethod
    def _task_attempt_record(cls, row: Mapping[str, Any]) -> dict[str, Any]:
        decoded_error = cls._decoded_json(row.get("error_json"))
        if isinstance(decoded_error, Mapping):
            error = str(decoded_error.get("message") or cls._json(dict(decoded_error)))
        elif decoded_error is None:
            error = None
        else:
            error = str(decoded_error)
        return {
            "id": row["id"],
            "task_id": row["task_id"],
            "step_id": row["step_id"],
            "run_id": row.get("run_id"),
            "number": int(row["attempt"]),
            "status": row["status"],
            "started_at": row["created_at"],
            "completed_at": row["completed_at"],
            "error": error,
        }

    @classmethod
    def _task_intervention_record(cls, row: Mapping[str, Any]) -> dict[str, Any]:
        payload = cls._decoded_json(row.get("payload_json") or "{}")
        return {
            "id": row["id"],
            "task_id": row["task_id"],
            "kind": row["kind"],
            "status": row["status"],
            "message": row["content"],
            "target_run_id": payload.get("target_run_id"),
            "created_at": row["created_at"],
            "applied_at": row["applied_at"],
        }

    @classmethod
    def _task_checkpoint_record(cls, row: Mapping[str, Any]) -> dict[str, Any]:
        state = cls._decoded_json(row["state_json"])
        completed = [
            str(step["id"])
            for step in state.get("steps", [])
            if step.get("status") in {"completed", "skipped"}
        ]
        after_step_id = completed[-1] if completed else None
        return {
            "id": row["id"],
            "task_id": row["task_id"],
            "sequence": int(row["sequence"]),
            "task_revision": int(row["task_revision"]),
            "after_step_id": after_step_id,
            "next_step_id": state.get("task", {}).get("current_step_id"),
            "completed_step_ids": completed,
            "created_at": row["created_at"],
        }

    def _get_task_locked(
        self,
        workspace_id: str,
        task_id: str,
        *,
        for_update: bool = False,
    ) -> Mapping[str, Any]:
        suffix = " FOR UPDATE" if for_update else ""
        row = self._one(
            "SELECT * FROM tasks WHERE workspace_id = ? AND id = ?" + suffix,
            (workspace_id, task_id),
        )
        if row is None:
            raise RepositoryConflict("Task does not exist in this Workspace")
        return row

    @staticmethod
    def _assert_task_revision(
        task: Mapping[str, Any],
        expected_revision: int,
    ) -> None:
        current_revision = int(task["revision"])
        if expected_revision != current_revision:
            raise TaskRevisionConflict(current_revision, str(task["status"]))

    @staticmethod
    def _require_task_status(
        task: Mapping[str, Any],
        command: str,
        allowed: set[str],
    ) -> None:
        current = str(task["status"])
        if current not in allowed:
            raise TaskTransitionConflict(command, current)

    def _task_checkpoint_state_locked(
        self,
        workspace_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        task = self._get_task_locked(workspace_id, task_id)
        plan = None
        steps: list[dict[str, Any]] = []
        if task["current_plan_id"] is not None:
            plan_row = self._one(
                """SELECT id, generation, status FROM task_plans
                WHERE workspace_id = ? AND task_id = ? AND id = ?""",
                (workspace_id, task_id, task["current_plan_id"]),
            )
            plan = dict(plan_row) if plan_row else None
            step_rows = self._all(
                """SELECT id, step_key, position, status, attempt_count
                FROM task_steps
                WHERE workspace_id = ? AND task_id = ? AND plan_id = ?
                ORDER BY position""",
                (workspace_id, task_id, task["current_plan_id"]),
            )
            steps = [dict(row) for row in step_rows]
        return {
            "task": {
                "id": task["id"],
                "workspace_id": task["workspace_id"],
                "thread_id": task["thread_id"],
                "status": task["status"],
                "revision": int(task["revision"]),
                "current_plan_id": task["current_plan_id"],
                "current_step_id": task["current_step_id"],
                "model_override": task.get("model_override"),
                "reasoning_effort": task.get("reasoning_effort"),
            },
            "plan": plan,
            "steps": steps,
        }

    def _record_task_transition_locked(
        self,
        workspace_id: str,
        task: Mapping[str, Any],
        *,
        expected_revision: int,
        event_type: str,
        payload: dict[str, Any],
        changes: dict[str, Any] | None = None,
        checkpoint_step_id: str | None = None,
        checkpoint_attempt_id: str | None = None,
        events_before: list[tuple[str, dict[str, Any]]] | None = None,
        events_after: list[tuple[str, dict[str, Any]]] | None = None,
    ) -> tuple[Mapping[str, Any], dict[str, Any], dict[str, Any]]:
        """CAS Task state and append its event + checkpoint in the active transaction."""
        self._assert_task_revision(task, expected_revision)
        ordered_events = [*(events_before or []), (event_type, payload), *(events_after or [])]
        for candidate_type, candidate_payload in ordered_events:
            if not re.fullmatch(r"task[.][a-z0-9_.-]{1,80}", candidate_type):
                raise ValueError("Task event type is invalid")
            self._validate_no_raw_secrets(candidate_payload)
        allowed_change_columns = {
            "status",
            "goal",
            "current_plan_id",
            "current_step_id",
            "control_reason",
            "result_json",
            "error_json",
            "completed_at",
        }
        normalized_changes = changes or {}
        unknown = set(normalized_changes) - allowed_change_columns
        if unknown:
            raise ValueError(f"Unsupported Task transition fields: {sorted(unknown)}")
        updated_at = utc_now()
        assignments = [f"{column} = ?" for column in normalized_changes]
        values = list(normalized_changes.values())
        assignments.extend(
            [
                "revision = revision + 1",
                f"next_event_sequence = next_event_sequence + {len(ordered_events)}",
                "next_checkpoint_sequence = next_checkpoint_sequence + 1",
                "updated_at = ?",
            ]
        )
        values.extend(
            [
                updated_at,
                workspace_id,
                task["id"],
                expected_revision,
            ]
        )
        updated = self._one(
            f"""UPDATE tasks SET {', '.join(assignments)}
            WHERE workspace_id = ? AND id = ? AND revision = ?
            RETURNING *""",
            tuple(values),
        )
        if updated is None:
            current = self._get_task_locked(workspace_id, str(task["id"]), for_update=True)
            raise TaskRevisionConflict(int(current["revision"]), str(current["status"]))

        first_sequence = int(updated["next_event_sequence"]) - len(ordered_events) + 1
        canonical_event: dict[str, Any] | None = None
        for offset, (candidate_type, candidate_payload) in enumerate(ordered_events):
            candidate_id = new_id("tevt")
            candidate_sequence = first_sequence + offset
            materialized_payload = dict(candidate_payload)
            self.connection.execute(
                """INSERT INTO task_events
                (id, workspace_id, task_id, sequence, task_revision, type,
                 payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    candidate_id,
                    workspace_id,
                    task["id"],
                    candidate_sequence,
                    int(updated["revision"]),
                    candidate_type,
                    self._json(materialized_payload),
                    updated_at,
                ),
            )
            if candidate_type == event_type and candidate_payload is payload:
                canonical_event = {
                    "id": candidate_id,
                    "workspace_id": workspace_id,
                    "task_id": task["id"],
                    "sequence": candidate_sequence,
                    "type": candidate_type,
                    "timestamp": updated_at,
                    "payload": materialized_payload,
                }
        state = self._task_checkpoint_state_locked(workspace_id, str(task["id"]))
        checkpoint_id = new_id("tcp")
        checkpoint_sequence = int(updated["next_checkpoint_sequence"])
        self.connection.execute(
            """INSERT INTO task_checkpoints
            (id, workspace_id, task_id, step_id, attempt_id, sequence,
             task_revision, reason, state_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                checkpoint_id,
                workspace_id,
                task["id"],
                checkpoint_step_id,
                checkpoint_attempt_id,
                checkpoint_sequence,
                int(updated["revision"]),
                event_type,
                self._json(state),
                updated_at,
            ),
        )
        if canonical_event is None:
            raise RepositoryConflict("Canonical Task transition event was not persisted")
        checkpoint = {
            "id": checkpoint_id,
            "task_id": task["id"],
            "sequence": checkpoint_sequence,
            "task_revision": int(updated["revision"]),
            "reason": event_type,
            "state": state,
            "created_at": updated_at,
        }
        return updated, canonical_event, checkpoint

    def _set_task_dispatch_locked(
        self,
        workspace_id: str,
        task_id: str,
        status: str,
        *,
        available_at: str | None = None,
    ) -> None:
        if status not in {"ready", "blocked", "terminal"}:
            raise ValueError("Repository transitions can only release or block dispatch")
        now = utc_now()
        self.connection.execute(
            """INSERT INTO task_dispatch
            (task_id, workspace_id, status, available_at, lease_owner,
             lease_expires_at, attempt_count, revision, updated_at)
            VALUES (?, ?, ?, ?, NULL, NULL, 0, 0, ?)
            ON CONFLICT (task_id) DO UPDATE SET
                status = EXCLUDED.status,
                available_at = EXCLUDED.available_at,
                lease_owner = NULL,
                lease_expires_at = NULL,
                revision = task_dispatch.revision + 1,
                updated_at = EXCLUDED.updated_at""",
            (task_id, workspace_id, status, available_at or now, now),
        )

    def claim_task_dispatch(
        self,
        workspace_id: str,
        task_id: str,
        lease_owner: str,
        *,
        lease_seconds: int = 30,
    ) -> bool:
        """Claim one ready Task boundary for a single coordinator process."""
        normalized_owner = lease_owner.strip()
        if not normalized_owner or len(normalized_owner) > 200:
            raise ValueError("Task dispatch lease owner is invalid")
        if not 5 <= lease_seconds <= 300:
            raise ValueError("Task dispatch lease must be between 5 and 300 seconds")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires_at = (now_dt + timedelta(seconds=lease_seconds)).isoformat()
        with self.lock, self.connection:
            row = self._one(
                """SELECT * FROM task_dispatch
                WHERE workspace_id = ? AND task_id = ? FOR UPDATE""",
                (workspace_id, task_id),
            )
            if row is None:
                return False
            reclaimable = row["status"] == "ready" or (
                row["status"] == "claimed"
                and str(row["lease_expires_at"] or "") <= now
            )
            if not reclaimable:
                return row["status"] == "claimed" and row["lease_owner"] == normalized_owner
            cursor = self.connection.execute(
                """UPDATE task_dispatch SET status = 'claimed', lease_owner = ?,
                    lease_expires_at = ?, attempt_count = attempt_count + 1,
                    revision = revision + 1, updated_at = ?
                WHERE workspace_id = ? AND task_id = ? AND revision = ?""",
                (
                    normalized_owner,
                    expires_at,
                    now,
                    workspace_id,
                    task_id,
                    int(row["revision"]),
                ),
            )
            return cursor.rowcount == 1

    def renew_task_dispatch(
        self,
        workspace_id: str,
        task_id: str,
        lease_owner: str,
        *,
        lease_seconds: int = 30,
    ) -> bool:
        if not 5 <= lease_seconds <= 300:
            raise ValueError("Task dispatch lease must be between 5 and 300 seconds")
        now_dt = datetime.now(timezone.utc)
        with self.lock, self.connection:
            cursor = self.connection.execute(
                """UPDATE task_dispatch SET lease_expires_at = ?, updated_at = ?,
                    revision = revision + 1
                WHERE workspace_id = ? AND task_id = ? AND status = 'claimed'
                  AND lease_owner = ?""",
                (
                    (now_dt + timedelta(seconds=lease_seconds)).isoformat(),
                    now_dt.isoformat(),
                    workspace_id,
                    task_id,
                    lease_owner,
                ),
            )
        return cursor.rowcount == 1

    def wake_task_dispatch(self, workspace_id: str, task_id: str) -> bool:
        """Release a blocked running Task after an external Run reaches a boundary."""
        with self.lock, self.connection:
            task = self._get_task_locked(workspace_id, task_id, for_update=True)
            if task["status"] != "running":
                return False
            self._set_task_dispatch_locked(workspace_id, task_id, "ready")
        return True

    def _block_task_dispatch_locked(self, workspace_id: str, task_id: str) -> None:
        """Keep an active worker lease while a Step Run is in flight."""
        row = self._one(
            """SELECT * FROM task_dispatch
            WHERE workspace_id = ? AND task_id = ? FOR UPDATE""",
            (workspace_id, task_id),
        )
        if row is not None and row["status"] == "claimed":
            return
        self._set_task_dispatch_locked(workspace_id, task_id, "blocked")

    def _hydrate_task_locked(
        self,
        workspace_id: str,
        task_id: str,
    ) -> dict[str, Any] | None:
        row = self._one(
            "SELECT * FROM tasks WHERE workspace_id = ? AND id = ?",
            (workspace_id, task_id),
        )
        if row is None:
            return None
        result = self._task_record(row)
        result["plan"] = None
        if row["current_plan_id"] is not None:
            plan_row = self._one(
                """SELECT * FROM task_plans
                WHERE workspace_id = ? AND task_id = ? AND id = ?""",
                (workspace_id, task_id, row["current_plan_id"]),
            )
            if plan_row is not None:
                plan = {
                    "id": plan_row["id"],
                    "task_id": plan_row["task_id"],
                    "updated_at": plan_row["updated_at"],
                }
                plan_steps: list[dict[str, Any]] = []
                for item in self._all(
                    """SELECT * FROM task_steps
                    WHERE workspace_id = ? AND task_id = ? AND plan_id = ?
                    ORDER BY position""",
                    (workspace_id, task_id, row["current_plan_id"]),
                ):
                    step = self._task_step_record(item)
                    step["attempts"] = [
                        self._task_attempt_record(attempt)
                        for attempt in self._all(
                            """SELECT a.*, l.run_id FROM task_step_attempts a
                            LEFT JOIN task_run_links l
                              ON l.workspace_id = a.workspace_id
                             AND l.attempt_id = a.id
                            WHERE a.workspace_id = ? AND a.task_id = ? AND a.step_id = ?
                            ORDER BY a.attempt""",
                            (workspace_id, task_id, step["id"]),
                        )
                    ]
                    plan_steps.append(step)
                plan["steps"] = plan_steps
                result["plan"] = plan
        checkpoint = self._one(
            """SELECT * FROM task_checkpoints
            WHERE workspace_id = ? AND task_id = ?
            ORDER BY sequence DESC LIMIT 1""",
            (workspace_id, task_id),
        )
        result["latest_checkpoint"] = (
            self._task_checkpoint_record(checkpoint) if checkpoint else None
        )
        pending_intervention = self._one(
            """SELECT * FROM task_interventions
            WHERE workspace_id = ? AND task_id = ? AND status = 'pending'
            ORDER BY created_at, id LIMIT 1""",
            (workspace_id, task_id),
        )
        result["pending_intervention"] = (
            self._task_intervention_record(pending_intervention)
            if pending_intervention
            else None
        )
        return result

    def create_task(
        self,
        workspace_id: str,
        thread_id: str,
        goal: str,
        *,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
        model_override: str | None = None,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        normalized_goal = goal.strip()
        normalized_title = (title or normalized_goal[:120]).strip()
        if not normalized_goal or len(normalized_goal) > 4_000:
            raise ValueError("Task goal must contain between 1 and 4000 characters")
        if not normalized_title or len(normalized_title) > 160:
            raise ValueError("Task title must contain between 1 and 160 characters")
        if model_override is not None and (
            not 1 <= len(model_override) <= 200
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", model_override) is None
        ):
            raise ValueError("Task model_override is invalid")
        if reasoning_effort is not None and reasoning_effort not in {
            "none", "low", "medium", "high"
        }:
            raise ValueError("Task reasoning_effort is invalid")
        safe_metadata = metadata or {}
        self._validate_no_raw_secrets(safe_metadata)
        task_id, created_at = new_id("task"), utc_now()
        with self.lock, self.connection:
            thread = self._one(
                "SELECT id FROM threads WHERE workspace_id = ? AND id = ? FOR UPDATE",
                (workspace_id, thread_id),
            )
            if thread is None:
                raise RepositoryConflict("Thread does not exist in this Workspace")
            self.connection.execute(
                """INSERT INTO tasks
                (id, workspace_id, thread_id, title, goal, status, revision,
                 next_event_sequence, next_checkpoint_sequence, metadata_json,
                 created_at, updated_at, model_override, reasoning_effort)
                VALUES (?, ?, ?, ?, ?, 'draft', 0, 0, 0, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    workspace_id,
                    thread_id,
                    normalized_title,
                    normalized_goal,
                    self._json(safe_metadata),
                    created_at,
                    created_at,
                    model_override,
                    reasoning_effort,
                ),
            )
            plan_id = new_id("plan")
            self.connection.execute(
                """INSERT INTO task_plans
                (id, workspace_id, task_id, generation, status, goal_snapshot,
                 created_at, updated_at)
                VALUES (?, ?, ?, 1, 'active', ?, ?, ?)""",
                (
                    plan_id,
                    workspace_id,
                    task_id,
                    normalized_goal,
                    created_at,
                    created_at,
                ),
            )
            self.connection.execute(
                """UPDATE tasks SET current_plan_id = ?
                WHERE workspace_id = ? AND id = ?""",
                (plan_id, workspace_id, task_id),
            )
            self._set_task_dispatch_locked(workspace_id, task_id, "blocked")
            task = self._get_task_locked(workspace_id, task_id, for_update=True)
            self._record_task_transition_locked(
                workspace_id,
                task,
                expected_revision=0,
                event_type="task.created",
                payload={
                    "title": normalized_title,
                    "goal": normalized_goal,
                    "plan_id": plan_id,
                    "model_override": model_override,
                    "reasoning_effort": reasoning_effort,
                },
            )
        return self.get_task(workspace_id, task_id) or {}

    def get_task(self, workspace_id: str, task_id: str) -> dict[str, Any] | None:
        return self._hydrate_task_locked(workspace_id, task_id)

    @classmethod
    def _task_command_record(cls, row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["result_revision"] = int(result["result_revision"])
        result["request"] = cls._decoded_json(result.pop("request_json"))
        result["result"] = cls._decoded_json(result.pop("result_json"))
        result.pop("request_sha256", None)
        return result

    def get_task_command(
        self,
        workspace_id: str,
        task_id: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        row = self._one(
            """SELECT * FROM task_commands
            WHERE workspace_id = ? AND task_id = ? AND idempotency_key = ?""",
            (workspace_id, task_id, idempotency_key),
        )
        return self._task_command_record(row) if row else None

    def execute_task_command(
        self,
        workspace_id: str,
        task_id: str,
        command: dict[str, Any],
        *,
        include_replay_metadata: bool = False,
    ) -> dict[str, Any]:
        """Execute an operator command and persist its idempotency result atomically."""
        name = str(command.get("command") or "")
        idempotency_key = str(command.get("idempotency_key") or "").strip()
        expected_revision = command.get("expected_revision")
        expected_status = command.get("expected_status")
        step_id = command.get("step_id")
        message = command.get("message")
        if name not in {
            "start",
            "pause",
            "resume",
            "cancel",
            "retry",
            "queue",
            "steer",
            "interrupt",
        }:
            raise ValueError("Task command is unsupported")
        if not 1 <= len(idempotency_key) <= 160 or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]*", idempotency_key
        ):
            raise ValueError("Task idempotency key is invalid")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
            raise ValueError("Task command expected_revision must be an integer")
        if step_id is not None:
            step_id = str(step_id)
        if message is not None:
            message = str(message).strip()
        if name == "retry" and not step_id:
            raise ValueError("Retry commands require step_id")
        if name != "retry" and step_id is not None:
            raise ValueError("step_id is only valid for retry commands")
        if name in {"queue", "steer"} and not message:
            raise ValueError(f"{name} commands require a message")
        self._validate_no_raw_secrets(command)
        serialized = json.dumps(
            command,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        request_sha256 = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        with self.lock, self.connection:
            task = self._get_task_locked(workspace_id, task_id, for_update=True)
            existing = self._one(
                """SELECT * FROM task_commands
                WHERE workspace_id = ? AND task_id = ? AND idempotency_key = ?""",
                (workspace_id, task_id, idempotency_key),
            )
            if existing is not None:
                if (
                    str(existing["command"]) != name
                    or str(existing["request_sha256"]) != request_sha256
                ):
                    raise RepositoryConflict(
                        "Task idempotency key was already used for a different command"
                    )
                replayed = self._decoded_json(existing["result_json"])
                return (
                    {"task": replayed, "replayed": True}
                    if include_replay_metadata
                    else replayed
                )

            self._assert_task_revision(task, expected_revision)
            if expected_status is not None and str(task["status"]) != str(expected_status):
                raise RepositoryConflict(
                    "Task status changed while the command was in flight"
                )

            event_type: str
            payload: dict[str, Any]
            changes: dict[str, Any]
            checkpoint_step_id = (
                str(task["current_step_id"])
                if task["current_step_id"] is not None
                else None
            )
            checkpoint_attempt_id: str | None = None
            if name == "start":
                self._require_task_status(task, "start", {"ready"})
                if task["current_step_id"] is None:
                    raise RepositoryConflict("Task requires a non-empty plan before start")
                event_type = "task.started"
                payload = {
                    "plan_id": task["current_plan_id"],
                    "step_id": task["current_step_id"],
                }
                changes = {"status": "running", "control_reason": None}
                self._set_task_dispatch_locked(workspace_id, task_id, "ready")
            elif name == "pause":
                self._require_task_status(
                    task,
                    "request pause for",
                    {"running", "waiting_for_approval", "waiting_for_user"},
                )
                event_type = "task.pause_requested"
                payload = {"reason": message}
                changes = {"status": "pause_requested", "control_reason": message}
                self._set_task_dispatch_locked(workspace_id, task_id, "blocked")
            elif name == "resume":
                self._require_task_status(
                    task,
                    "resume",
                    {"paused", "waiting_for_approval", "waiting_for_user"},
                )
                if checkpoint_step_id is not None:
                    step = self._active_task_step_locked(
                        workspace_id,
                        task,
                        checkpoint_step_id,
                    )
                    attempt = self._latest_task_attempt_locked(
                        workspace_id,
                        task_id,
                        checkpoint_step_id,
                    )
                    if step["status"] in {"waiting_for_approval", "waiting_for_user"}:
                        self.connection.execute(
                            """UPDATE task_steps SET status = 'running'
                            WHERE workspace_id = ? AND task_id = ? AND id = ?""",
                            (workspace_id, task_id, checkpoint_step_id),
                        )
                    if attempt is not None and attempt["status"] in {
                        "waiting_for_approval",
                        "waiting_for_user",
                    }:
                        checkpoint_attempt_id = str(attempt["id"])
                        self.connection.execute(
                            """UPDATE task_step_attempts SET status = 'running', updated_at = ?
                            WHERE workspace_id = ? AND task_id = ? AND id = ?""",
                            (
                                utc_now(),
                                workspace_id,
                                task_id,
                                checkpoint_attempt_id,
                            ),
                        )
                event_type = "task.resumed"
                payload = {"step_id": checkpoint_step_id}
                changes = {"status": "running", "control_reason": None}
                self._set_task_dispatch_locked(workspace_id, task_id, "ready")
            elif name == "cancel":
                self._require_task_status(
                    task,
                    "request cancellation for",
                    self._TASK_NONTERMINAL_STATUSES - {"cancel_requested"},
                )
                event_type = "task.cancel_requested"
                payload = {"reason": message}
                changes = {"status": "cancel_requested", "control_reason": message}
                self._set_task_dispatch_locked(workspace_id, task_id, "blocked")
            elif name == "retry":
                self._require_task_status(task, "retry a step for", {"waiting_for_user"})
                assert step_id is not None
                step = self._active_task_step_locked(workspace_id, task, step_id)
                if step["status"] != "failed":
                    raise RepositoryConflict("Only a failed Task step can be retried")
                self.connection.execute(
                    """UPDATE task_steps SET status = 'pending', completed_at = NULL,
                        output_json = NULL, evidence_json = '[]', error_json = NULL
                    WHERE workspace_id = ? AND task_id = ? AND id = ?""",
                    (workspace_id, task_id, step_id),
                )
                checkpoint_step_id = step_id
                event_type = "task.step.retry_requested"
                payload = {
                    "step_id": step_id,
                    "next_attempt": int(step["attempt_count"]) + 1,
                }
                changes = {
                    "status": "running",
                    "current_step_id": step_id,
                    "error_json": None,
                    "control_reason": None,
                }
                self._set_task_dispatch_locked(workspace_id, task_id, "ready")
            else:
                if name == "queue":
                    allowed = {
                        "running",
                        "waiting_for_approval",
                        "waiting_for_user",
                        "pause_requested",
                        "paused",
                    }
                else:
                    allowed = {"running", "waiting_for_approval", "waiting_for_user"}
                self._require_task_status(task, name, allowed)
                intervention_id = new_id("tint")
                content = message or "Interrupt requested."
                self.connection.execute(
                    """INSERT INTO task_interventions
                    (id, workspace_id, task_id, step_id, kind, status, content,
                     payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, 'pending', ?, '{}', ?)""",
                    (
                        intervention_id,
                        workspace_id,
                        task_id,
                        checkpoint_step_id,
                        name,
                        content,
                        utc_now(),
                    ),
                )
                event_type = {
                    "queue": "task.intervention.queued",
                    "steer": "task.intervention.steered",
                    "interrupt": "task.intervention.interrupt_requested",
                }[name]
                payload = {
                    "intervention_id": intervention_id,
                    "kind": name,
                    "step_id": checkpoint_step_id,
                    "content": content,
                }
                changes = (
                    {
                        "status": "pause_requested",
                        "control_reason": "user_interrupt",
                    }
                    if name == "interrupt"
                    else {}
                )
                if name == "interrupt":
                    self._set_task_dispatch_locked(workspace_id, task_id, "blocked")

            updated, _event, _checkpoint = self._record_task_transition_locked(
                workspace_id,
                task,
                expected_revision=expected_revision,
                event_type=event_type,
                payload=payload,
                changes=changes,
                checkpoint_step_id=checkpoint_step_id,
                checkpoint_attempt_id=checkpoint_attempt_id,
            )
            result_task = self._hydrate_task_locked(workspace_id, task_id)
            if result_task is None:
                raise RepositoryConflict("Task command result could not be materialized")
            command_id, created_at = new_id("tcmd"), utc_now()
            self.connection.execute(
                """INSERT INTO task_commands
                (id, workspace_id, task_id, idempotency_key, command, request_json,
                 request_sha256, result_revision, result_status, result_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    command_id,
                    workspace_id,
                    task_id,
                    idempotency_key,
                    name,
                    serialized,
                    request_sha256,
                    int(updated["revision"]),
                    updated["status"],
                    self._json(result_task),
                    created_at,
                ),
            )
        return (
            {"task": result_task, "replayed": False}
            if include_replay_metadata
            else result_task
        )

    def list_tasks(
        self,
        workspace_id: str,
        *,
        thread_id: str | None = None,
        statuses: tuple[str, ...] = (),
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 200:
            raise ValueError("Task list limit must be between 1 and 200")
        unknown = set(statuses) - (
            self._TASK_NONTERMINAL_STATUSES | self._TASK_TERMINAL_STATUSES
        )
        if unknown:
            raise ValueError(f"Unknown Task statuses: {sorted(unknown)}")
        clauses = ["workspace_id = ?"]
        values: list[Any] = [workspace_id]
        if thread_id is not None:
            clauses.append("thread_id = ?")
            values.append(thread_id)
        if statuses:
            clauses.append("status IN (" + ",".join("?" for _ in statuses) + ")")
            values.extend(statuses)
        values.append(limit)
        rows = self._all(
            f"""SELECT * FROM tasks WHERE {' AND '.join(clauses)}
            ORDER BY updated_at DESC, id DESC LIMIT ?""",
            tuple(values),
        )
        return [
            hydrated
            for row in rows
            if (hydrated := self._hydrate_task_locked(workspace_id, str(row["id"])))
            is not None
        ]

    @staticmethod
    def _validated_task_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(steps) > 64:
            raise ValueError("A Task plan can contain at most 64 steps")
        normalized: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for position, step in enumerate(steps):
            step_key = str(step.get("key") or step.get("step_key") or f"step-{position + 1}")
            title = str(step.get("title") or "").strip()
            description = str(step.get("description") or "").strip()
            kind = str(step.get("kind") or "agent")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", step_key):
                raise ValueError(f"Task step key is invalid: {step_key}")
            if step_key in seen_keys:
                raise ValueError(f"Task step key is duplicated: {step_key}")
            if not title or len(title) > 160:
                raise ValueError("Task step title must contain between 1 and 160 characters")
            if len(description) > 2_000:
                raise ValueError("Task step description exceeds 2000 characters")
            if kind != "agent":
                raise ValueError("The first Task runtime supports agent steps only")
            seen_keys.add(step_key)
            normalized.append(
                {
                    "id": new_id("step"),
                    "step_key": step_key,
                    "position": position,
                    "title": title,
                    "description": description,
                    "kind": kind,
                    "input": step.get("input") or {},
                }
            )
        return normalized

    def replace_task_plan(
        self,
        workspace_id: str,
        task_id: str,
        *,
        expected_revision: int,
        steps: list[dict[str, Any]],
        goal: str | None = None,
    ) -> dict[str, Any]:
        normalized_steps = self._validated_task_steps(steps)
        self._validate_no_raw_secrets(normalized_steps)
        normalized_goal = str(goal).strip() if goal is not None else None
        if normalized_goal is not None and not 1 <= len(normalized_goal) <= 4_000:
            raise ValueError("Task goal must contain between 1 and 4000 characters")
        now = utc_now()
        with self.lock, self.connection:
            task = self._get_task_locked(workspace_id, task_id, for_update=True)
            self._assert_task_revision(task, expected_revision)
            self._require_task_status(
                task,
                "replace plan for",
                {"draft", "planning", "ready", "paused"},
            )
            if task["current_plan_id"] is not None:
                self.connection.execute(
                    """UPDATE task_plans SET status = 'superseded', superseded_at = ?,
                        updated_at = ?
                    WHERE workspace_id = ? AND task_id = ? AND id = ? AND status = 'active'""",
                    (now, now, workspace_id, task_id, task["current_plan_id"]),
                )
            generation_row = self._one(
                """SELECT COALESCE(MAX(generation), 0) + 1 AS value
                FROM task_plans WHERE workspace_id = ? AND task_id = ?""",
                (workspace_id, task_id),
            )
            generation = int(generation_row["value"]) if generation_row else 1
            plan_id = new_id("plan")
            self.connection.execute(
                """INSERT INTO task_plans
                (id, workspace_id, task_id, generation, status, goal_snapshot,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?, ?)""",
                (
                    plan_id,
                    workspace_id,
                    task_id,
                    generation,
                    normalized_goal or task["goal"],
                    now,
                    now,
                ),
            )
            for step in normalized_steps:
                self.connection.execute(
                    """INSERT INTO task_steps
                    (id, workspace_id, task_id, plan_id, step_key, position, title,
                     description, kind, status, attempt_count, input_json,
                     evidence_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, '[]', ?)""",
                    (
                        step["id"],
                        workspace_id,
                        task_id,
                        plan_id,
                        step["step_key"],
                        step["position"],
                        step["title"],
                        step["description"],
                        step["kind"],
                        self._json(step["input"]),
                        now,
                    ),
                )
            next_status = (
                "paused"
                if task["status"] == "paused"
                else ("ready" if normalized_steps else "planning")
            )
            self._set_task_dispatch_locked(
                workspace_id,
                task_id,
                "ready" if next_status == "ready" else "blocked",
            )
            self._record_task_transition_locked(
                workspace_id,
                task,
                expected_revision=expected_revision,
                event_type="task.plan.updated",
                payload={
                    "plan_id": plan_id,
                    "generation": generation,
                    "steps": [
                        {
                            "id": step["id"],
                            "key": step["step_key"],
                            "position": step["position"],
                            "title": step["title"],
                        }
                        for step in normalized_steps
                    ],
                },
                changes={
                    "status": next_status,
                    **({"goal": normalized_goal} if normalized_goal is not None else {}),
                    "current_plan_id": plan_id,
                    "current_step_id": (
                        normalized_steps[0]["id"] if normalized_steps else None
                    ),
                    "control_reason": None,
                },
                checkpoint_step_id=(
                    normalized_steps[0]["id"] if normalized_steps else None
                ),
            )
        return self.get_task(workspace_id, task_id) or {}

    def start_task(
        self,
        workspace_id: str,
        task_id: str,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        with self.lock, self.connection:
            task = self._get_task_locked(workspace_id, task_id, for_update=True)
            self._assert_task_revision(task, expected_revision)
            self._require_task_status(task, "start", {"ready"})
            if task["current_plan_id"] is None or task["current_step_id"] is None:
                raise RepositoryConflict("Task requires a non-empty active plan before start")
            self._set_task_dispatch_locked(workspace_id, task_id, "ready")
            self._record_task_transition_locked(
                workspace_id,
                task,
                expected_revision=expected_revision,
                event_type="task.started",
                payload={
                    "plan_id": task["current_plan_id"],
                    "step_id": task["current_step_id"],
                },
                changes={"status": "running", "control_reason": None},
                checkpoint_step_id=str(task["current_step_id"]),
            )
        return self.get_task(workspace_id, task_id) or {}

    def _active_task_step_locked(
        self,
        workspace_id: str,
        task: Mapping[str, Any],
        step_id: str,
        *,
        for_update: bool = True,
    ) -> Mapping[str, Any]:
        suffix = " FOR UPDATE" if for_update else ""
        step = self._one(
            """SELECT * FROM task_steps
            WHERE workspace_id = ? AND task_id = ? AND plan_id = ? AND id = ?"""
            + suffix,
            (workspace_id, task["id"], task["current_plan_id"], step_id),
        )
        if step is None:
            raise RepositoryConflict("Task step does not belong to the active plan")
        return step

    def _latest_task_attempt_locked(
        self,
        workspace_id: str,
        task_id: str,
        step_id: str,
        *,
        for_update: bool = True,
    ) -> Mapping[str, Any] | None:
        suffix = " FOR UPDATE" if for_update else ""
        return self._one(
            """SELECT * FROM task_step_attempts
            WHERE workspace_id = ? AND task_id = ? AND step_id = ?
            ORDER BY attempt DESC LIMIT 1"""
            + suffix,
            (workspace_id, task_id, step_id),
        )

    def start_task_step(
        self,
        workspace_id: str,
        task_id: str,
        step_id: str,
        *,
        expected_revision: int,
        input: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        safe_input = input or {}
        self._validate_no_raw_secrets(safe_input)
        now = utc_now()
        with self.lock, self.connection:
            task = self._get_task_locked(workspace_id, task_id, for_update=True)
            self._assert_task_revision(task, expected_revision)
            self._require_task_status(task, "start a step for", {"running"})
            if str(task["current_step_id"]) != step_id:
                raise RepositoryConflict("Only the current sequential Task step can start")
            step = self._active_task_step_locked(workspace_id, task, step_id)
            if step["status"] != "pending":
                raise RepositoryConflict("Task step is not pending")
            incomplete_prior = self._one(
                """SELECT id FROM task_steps
                WHERE workspace_id = ? AND task_id = ? AND plan_id = ?
                  AND position < ? AND status NOT IN ('completed', 'skipped')
                ORDER BY position LIMIT 1""",
                (
                    workspace_id,
                    task_id,
                    task["current_plan_id"],
                    int(step["position"]),
                ),
            )
            if incomplete_prior is not None:
                raise RepositoryConflict("A prior sequential Task step is incomplete")
            attempt_number = int(step["attempt_count"]) + 1
            attempt_id = new_id("attempt")
            merged_input = self._decoded_json(step["input_json"])
            if safe_input:
                merged_input = {**merged_input, **safe_input}
            self.connection.execute(
                """UPDATE task_steps
                SET status = 'running', attempt_count = ?, input_json = ?,
                    started_at = COALESCE(started_at, ?), completed_at = NULL,
                    output_json = NULL, evidence_json = '[]', error_json = NULL
                WHERE workspace_id = ? AND task_id = ? AND id = ?""",
                (
                    attempt_number,
                    self._json(merged_input),
                    now,
                    workspace_id,
                    task_id,
                    step_id,
                ),
            )
            self.connection.execute(
                """INSERT INTO task_step_attempts
                (id, workspace_id, task_id, step_id, attempt, status, input_json,
                 evidence_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'running', ?, '[]', ?, ?)""",
                (
                    attempt_id,
                    workspace_id,
                    task_id,
                    step_id,
                    attempt_number,
                    self._json(merged_input),
                    now,
                    now,
                ),
            )
            self._block_task_dispatch_locked(workspace_id, task_id)
            self._record_task_transition_locked(
                workspace_id,
                task,
                expected_revision=expected_revision,
                event_type="task.step.started",
                payload={
                    "step_id": step_id,
                    "attempt_id": attempt_id,
                    "attempt": attempt_number,
                },
                changes={"current_step_id": step_id},
                checkpoint_step_id=step_id,
                checkpoint_attempt_id=attempt_id,
                events_after=[
                    (
                        "task.attempt.started",
                        {
                            "step_id": step_id,
                            "attempt_id": attempt_id,
                            "attempt": attempt_number,
                        },
                    )
                ],
            )
        return self.get_task(workspace_id, task_id) or {}

    @staticmethod
    def _normalized_task_evidence(
        task_id: str,
        step_id: str,
        attempt_id: str,
        evidence: list[dict[str, Any]],
        *,
        created_at: str,
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        allowed_kinds = {"artifact", "citation", "tool_result", "checkpoint", "note"}
        for index, item in enumerate(evidence):
            source_uri = item.get("source_uri") or item.get("uri")
            kind = str(item.get("kind") or ("citation" if source_uri else "note"))
            if kind not in allowed_kinds:
                raise ValueError(f"Unsupported Task evidence kind: {kind}")
            label = str(item.get("label") or f"Evidence {index + 1}").strip()
            summary = str(item.get("summary") or "").strip()
            if not 1 <= len(label) <= 200:
                raise ValueError("Task evidence label must contain between 1 and 200 characters")
            if len(summary) > 4_000:
                raise ValueError("Task evidence summary exceeds 4000 characters")
            normalized.append(
                {
                    "id": str(item.get("id") or new_id("tevd")),
                    "task_id": task_id,
                    "step_id": step_id,
                    "attempt_id": str(item.get("attempt_id") or attempt_id),
                    "kind": kind,
                    "label": label,
                    "summary": summary,
                    "resource_id": item.get("resource_id"),
                    "source_uri": source_uri,
                    "created_at": str(item.get("created_at") or created_at),
                }
            )
        return normalized

    def complete_task_step(
        self,
        workspace_id: str,
        task_id: str,
        step_id: str,
        *,
        expected_revision: int,
        output: dict[str, Any] | None = None,
        evidence: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        safe_output = output or {}
        safe_evidence = evidence or []
        self._validate_no_raw_secrets(safe_output)
        self._validate_no_raw_secrets(safe_evidence)
        completed_at = utc_now()
        with self.lock, self.connection:
            task = self._get_task_locked(workspace_id, task_id, for_update=True)
            self._assert_task_revision(task, expected_revision)
            self._require_task_status(
                task,
                "complete a step for",
                {
                    "running",
                    "waiting_for_approval",
                    "waiting_for_user",
                    "pause_requested",
                    "cancel_requested",
                },
            )
            step = self._active_task_step_locked(workspace_id, task, step_id)
            if step["status"] not in {
                "running",
                "waiting_for_approval",
                "waiting_for_user",
            }:
                raise RepositoryConflict("Task step is not active")
            attempt = self._latest_task_attempt_locked(
                workspace_id,
                task_id,
                step_id,
            )
            if attempt is None or attempt["status"] in self._TASK_ATTEMPT_TERMINAL_STATUSES:
                raise RepositoryConflict("Task step has no active attempt")
            normalized_evidence = self._normalized_task_evidence(
                task_id,
                step_id,
                str(attempt["id"]),
                safe_evidence,
                created_at=completed_at,
            )
            self._validate_no_raw_secrets(normalized_evidence)
            self.connection.execute(
                """UPDATE task_step_attempts
                SET status = 'completed', output_json = ?, evidence_json = ?,
                    error_json = NULL, updated_at = ?, completed_at = ?
                WHERE workspace_id = ? AND task_id = ? AND id = ?""",
                (
                    self._json(safe_output),
                    self._json(normalized_evidence),
                    completed_at,
                    completed_at,
                    workspace_id,
                    task_id,
                    attempt["id"],
                ),
            )
            self.connection.execute(
                """UPDATE task_steps
                SET status = 'completed', output_json = ?, evidence_json = ?,
                    error_json = NULL, completed_at = ?
                WHERE workspace_id = ? AND task_id = ? AND id = ?""",
                (
                    self._json(safe_output),
                    self._json(normalized_evidence),
                    completed_at,
                    workspace_id,
                    task_id,
                    step_id,
                ),
            )
            next_step = self._one(
                """SELECT id FROM task_steps
                WHERE workspace_id = ? AND task_id = ? AND plan_id = ?
                  AND position > ? AND status = 'pending'
                ORDER BY position LIMIT 1""",
                (
                    workspace_id,
                    task_id,
                    task["current_plan_id"],
                    int(step["position"]),
                ),
            )
            next_step_id = str(next_step["id"]) if next_step else None
            self._set_task_dispatch_locked(
                workspace_id,
                task_id,
                (
                    "ready"
                    if next_step_id and task["status"] == "running"
                    else "blocked"
                ),
            )
            self._record_task_transition_locked(
                workspace_id,
                task,
                expected_revision=expected_revision,
                event_type="task.step.completed",
                payload={
                    "step_id": step_id,
                    "attempt_id": attempt["id"],
                    "next_step_id": next_step_id,
                    "output": safe_output,
                    "evidence": normalized_evidence,
                },
                changes={
                    "status": (
                        task["status"]
                        if task["status"] in {"pause_requested", "cancel_requested"}
                        else "running"
                    ),
                    "current_step_id": next_step_id,
                    "control_reason": (
                        task["control_reason"]
                        if task["status"] in {"pause_requested", "cancel_requested"}
                        else None
                    ),
                },
                checkpoint_step_id=step_id,
                checkpoint_attempt_id=str(attempt["id"]),
                events_before=[
                    (
                        "task.attempt.completed",
                        {
                            "step_id": step_id,
                            "attempt_id": attempt["id"],
                            "attempt": attempt["attempt"],
                        },
                    )
                ],
            )
        return self.get_task(workspace_id, task_id) or {}

    def fail_task_step(
        self,
        workspace_id: str,
        task_id: str,
        step_id: str,
        *,
        expected_revision: int,
        error: dict[str, Any],
    ) -> dict[str, Any]:
        self._validate_no_raw_secrets(error)
        completed_at = utc_now()
        with self.lock, self.connection:
            task = self._get_task_locked(workspace_id, task_id, for_update=True)
            self._assert_task_revision(task, expected_revision)
            self._require_task_status(
                task,
                "fail a step for",
                {
                    "running",
                    "waiting_for_approval",
                    "waiting_for_user",
                    "pause_requested",
                    "cancel_requested",
                },
            )
            step = self._active_task_step_locked(workspace_id, task, step_id)
            if step["status"] not in {
                "running",
                "waiting_for_approval",
                "waiting_for_user",
            }:
                raise RepositoryConflict("Task step is not active")
            attempt = self._latest_task_attempt_locked(
                workspace_id,
                task_id,
                step_id,
            )
            if attempt is None or attempt["status"] in self._TASK_ATTEMPT_TERMINAL_STATUSES:
                raise RepositoryConflict("Task step has no active attempt")
            error_json = self._json(error)
            self.connection.execute(
                """UPDATE task_step_attempts
                SET status = 'failed', error_json = ?, updated_at = ?, completed_at = ?
                WHERE workspace_id = ? AND task_id = ? AND id = ?""",
                (
                    error_json,
                    completed_at,
                    completed_at,
                    workspace_id,
                    task_id,
                    attempt["id"],
                ),
            )
            self.connection.execute(
                """UPDATE task_steps
                SET status = 'failed', error_json = ?, completed_at = ?
                WHERE workspace_id = ? AND task_id = ? AND id = ?""",
                (error_json, completed_at, workspace_id, task_id, step_id),
            )
            self._set_task_dispatch_locked(workspace_id, task_id, "blocked")
            self._record_task_transition_locked(
                workspace_id,
                task,
                expected_revision=expected_revision,
                event_type="task.step.failed",
                payload={
                    "step_id": step_id,
                    "attempt_id": attempt["id"],
                    "error": error,
                },
                changes={
                    "status": (
                        task["status"]
                        if task["status"] in {"pause_requested", "cancel_requested"}
                        else "waiting_for_user"
                    ),
                    "error_json": error_json,
                    "control_reason": (
                        task["control_reason"]
                        if task["status"] in {"pause_requested", "cancel_requested"}
                        else "step_failed"
                    ),
                },
                checkpoint_step_id=step_id,
                checkpoint_attempt_id=str(attempt["id"]),
                events_before=[
                    (
                        "task.attempt.failed",
                        {
                            "step_id": step_id,
                            "attempt_id": attempt["id"],
                            "attempt": attempt["attempt"],
                            "error": error,
                        },
                    )
                ],
            )
        return self.get_task(workspace_id, task_id) or {}

    def complete_task(
        self,
        workspace_id: str,
        task_id: str,
        *,
        expected_revision: int,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(result, dict) or not result:
            raise RepositoryConflict("Task completion requires a non-empty result")
        safe_result = result
        self._validate_no_raw_secrets(safe_result)
        completed_at = utc_now()
        with self.lock, self.connection:
            task = self._get_task_locked(workspace_id, task_id, for_update=True)
            self._assert_task_revision(task, expected_revision)
            self._require_task_status(task, "complete", {"running"})
            incomplete = self._one(
                """SELECT id FROM task_steps
                WHERE workspace_id = ? AND task_id = ? AND plan_id = ?
                  AND status NOT IN ('completed', 'skipped')
                ORDER BY position LIMIT 1""",
                (workspace_id, task_id, task["current_plan_id"]),
            )
            if incomplete is not None:
                raise RepositoryConflict("Task cannot complete while a plan step is incomplete")
            pending_intervention = self._one(
                """SELECT * FROM task_interventions
                WHERE workspace_id = ? AND task_id = ? AND status = 'pending'
                  AND kind IN ('queue', 'steer')
                ORDER BY created_at, id LIMIT 1 FOR UPDATE""",
                (workspace_id, task_id),
            )
            if pending_intervention is not None:
                count_row = self._one(
                    """SELECT COUNT(*) AS value, COALESCE(MAX(position), -1) AS max_position
                    FROM task_steps
                    WHERE workspace_id = ? AND task_id = ? AND plan_id = ?""",
                    (workspace_id, task_id, task["current_plan_id"]),
                )
                step_count = int(count_row["value"]) if count_row else 0
                if step_count >= 64:
                    self.connection.execute(
                        """UPDATE task_interventions SET status = 'rejected'
                        WHERE workspace_id = ? AND id = ? AND status = 'pending'""",
                        (workspace_id, pending_intervention["id"]),
                    )
                    self._record_task_transition_locked(
                        workspace_id,
                        task,
                        expected_revision=expected_revision,
                        event_type="task.intervention.rejected",
                        payload={
                            "intervention_id": pending_intervention["id"],
                            "kind": pending_intervention["kind"],
                            "reason": "task_step_limit_reached",
                        },
                    )
                    task = self._get_task_locked(workspace_id, task_id, for_update=True)
                    expected_revision = int(task["revision"])
                else:
                    content = str(pending_intervention["content"]).strip()
                    first_line = next(
                        (line.strip() for line in content.splitlines() if line.strip()),
                        "Apply user guidance",
                    )
                    continuation_id = new_id("step")
                    position = int(count_row["max_position"]) + 1 if count_row else 0
                    self.connection.execute(
                        """INSERT INTO task_steps
                        (id, workspace_id, task_id, plan_id, step_key, position, title,
                         description, kind, status, attempt_count, input_json,
                         evidence_json, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'agent', 'pending', 0, ?, '[]', ?)""",
                        (
                            continuation_id,
                            workspace_id,
                            task_id,
                            task["current_plan_id"],
                            f"intervention-{pending_intervention['id']}",
                            position,
                            f"Apply guidance: {first_line}"[:160],
                            content[:2_000],
                            self._json(
                                {
                                    "intervention_id": pending_intervention["id"],
                                    "content": content,
                                }
                            ),
                            completed_at,
                        ),
                    )
                    self.connection.execute(
                        """UPDATE task_plans SET updated_at = ?
                        WHERE workspace_id = ? AND task_id = ? AND id = ?""",
                        (completed_at, workspace_id, task_id, task["current_plan_id"]),
                    )
                    plan_rows = self._all(
                        """SELECT id, position, title, description, status
                        FROM task_steps
                        WHERE workspace_id = ? AND task_id = ? AND plan_id = ?
                        ORDER BY position""",
                        (workspace_id, task_id, task["current_plan_id"]),
                    )
                    self._set_task_dispatch_locked(workspace_id, task_id, "ready")
                    self._record_task_transition_locked(
                        workspace_id,
                        task,
                        expected_revision=expected_revision,
                        event_type="task.plan.updated",
                        payload={
                            "plan_id": task["current_plan_id"],
                            "reason": "pending_intervention_continuation",
                            "intervention_id": pending_intervention["id"],
                            "steps": [
                                {
                                    "id": row["id"],
                                    "position": int(row["position"]),
                                    "title": row["title"],
                                    "description": row["description"],
                                    "status": row["status"],
                                }
                                for row in plan_rows
                            ],
                        },
                        changes={
                            "status": "running",
                            "current_step_id": continuation_id,
                            "control_reason": None,
                        },
                        checkpoint_step_id=continuation_id,
                    )
                    return self.get_task(workspace_id, task_id) or {}
            self.connection.execute(
                """UPDATE task_plans SET status = 'completed', updated_at = ?
                WHERE workspace_id = ? AND task_id = ? AND id = ?""",
                (completed_at, workspace_id, task_id, task["current_plan_id"]),
            )
            self._set_task_dispatch_locked(workspace_id, task_id, "terminal")
            self._record_task_transition_locked(
                workspace_id,
                task,
                expected_revision=expected_revision,
                event_type="task.completed",
                payload={"result": safe_result},
                changes={
                    "status": "completed",
                    "current_step_id": None,
                    "result_json": self._json(safe_result),
                    "error_json": None,
                    "control_reason": None,
                    "completed_at": completed_at,
                },
            )
        return self.get_task(workspace_id, task_id) or {}

    def fail_task(
        self,
        workspace_id: str,
        task_id: str,
        *,
        expected_revision: int,
        error: dict[str, Any],
    ) -> dict[str, Any]:
        self._validate_no_raw_secrets(error)
        completed_at = utc_now()
        with self.lock, self.connection:
            task = self._get_task_locked(workspace_id, task_id, for_update=True)
            self._assert_task_revision(task, expected_revision)
            self._require_task_status(
                task,
                "fail",
                {
                    "planning",
                    "ready",
                    "running",
                    "pause_requested",
                    "paused",
                    "waiting_for_approval",
                    "waiting_for_user",
                },
            )
            if task["current_step_id"] is not None:
                step = self._active_task_step_locked(
                    workspace_id,
                    task,
                    str(task["current_step_id"]),
                )
                if step["status"] not in self._TASK_STEP_TERMINAL_STATUSES:
                    self.connection.execute(
                        """UPDATE task_steps SET status = 'failed', error_json = ?,
                            completed_at = ?
                        WHERE workspace_id = ? AND task_id = ? AND id = ?""",
                        (
                            self._json(error),
                            completed_at,
                            workspace_id,
                            task_id,
                            step["id"],
                        ),
                    )
                    attempt = self._latest_task_attempt_locked(
                        workspace_id,
                        task_id,
                        str(step["id"]),
                    )
                    if (
                        attempt is not None
                        and attempt["status"]
                        not in self._TASK_ATTEMPT_TERMINAL_STATUSES
                    ):
                        self.connection.execute(
                            """UPDATE task_step_attempts
                            SET status = 'failed', error_json = ?, updated_at = ?,
                                completed_at = ?
                            WHERE workspace_id = ? AND task_id = ? AND id = ?""",
                            (
                                self._json(error),
                                completed_at,
                                completed_at,
                                workspace_id,
                                task_id,
                                attempt["id"],
                            ),
                        )
            self._set_task_dispatch_locked(workspace_id, task_id, "terminal")
            self._record_task_transition_locked(
                workspace_id,
                task,
                expected_revision=expected_revision,
                event_type="task.failed",
                payload={"error": error},
                changes={
                    "status": "failed",
                    "error_json": self._json(error),
                    "completed_at": completed_at,
                },
                checkpoint_step_id=(
                    str(task["current_step_id"])
                    if task["current_step_id"] is not None
                    else None
                ),
            )
        return self.get_task(workspace_id, task_id) or {}

    def request_task_pause(
        self,
        workspace_id: str,
        task_id: str,
        *,
        expected_revision: int,
        reason: str | None = None,
    ) -> dict[str, Any]:
        normalized_reason = reason.strip() if reason else None
        with self.lock, self.connection:
            task = self._get_task_locked(workspace_id, task_id, for_update=True)
            self._assert_task_revision(task, expected_revision)
            self._require_task_status(
                task,
                "request pause for",
                {"running", "waiting_for_approval", "waiting_for_user"},
            )
            self._set_task_dispatch_locked(workspace_id, task_id, "blocked")
            self._record_task_transition_locked(
                workspace_id,
                task,
                expected_revision=expected_revision,
                event_type="task.pause_requested",
                payload={"reason": normalized_reason},
                changes={
                    "status": "pause_requested",
                    "control_reason": normalized_reason,
                },
                checkpoint_step_id=(
                    str(task["current_step_id"])
                    if task["current_step_id"] is not None
                    else None
                ),
            )
        return self.get_task(workspace_id, task_id) or {}

    def _interrupt_active_attempt_locked(
        self,
        workspace_id: str,
        task: Mapping[str, Any],
        *,
        timestamp: str,
    ) -> str | None:
        if task["current_step_id"] is None:
            return None
        attempt = self._latest_task_attempt_locked(
            workspace_id,
            str(task["id"]),
            str(task["current_step_id"]),
        )
        if attempt is None or attempt["status"] in self._TASK_ATTEMPT_TERMINAL_STATUSES:
            return None
        self.connection.execute(
            """UPDATE task_step_attempts
            SET status = 'interrupted', updated_at = ?, completed_at = ?
            WHERE workspace_id = ? AND task_id = ? AND id = ?""",
            (
                timestamp,
                timestamp,
                workspace_id,
                task["id"],
                attempt["id"],
            ),
        )
        self.connection.execute(
            """UPDATE task_steps SET status = 'pending', completed_at = NULL,
                output_json = NULL, evidence_json = '[]', error_json = NULL
            WHERE workspace_id = ? AND task_id = ? AND id = ?
              AND status NOT IN ('completed', 'skipped')""",
            (workspace_id, task["id"], task["current_step_id"]),
        )
        return str(attempt["id"])

    def mark_task_paused(
        self,
        workspace_id: str,
        task_id: str,
        *,
        expected_revision: int,
        reason: str | None = None,
    ) -> dict[str, Any]:
        paused_at = utc_now()
        with self.lock, self.connection:
            task = self._get_task_locked(workspace_id, task_id, for_update=True)
            self._assert_task_revision(task, expected_revision)
            self._require_task_status(task, "mark paused", {"pause_requested"})
            attempt_id = self._interrupt_active_attempt_locked(
                workspace_id,
                task,
                timestamp=paused_at,
            )
            interrupts = self._all(
                """SELECT * FROM task_interventions
                WHERE workspace_id = ? AND task_id = ? AND status = 'pending'
                  AND kind = 'interrupt'
                ORDER BY created_at, id FOR UPDATE""",
                (workspace_id, task_id),
            )
            if interrupts:
                self.connection.execute(
                    """UPDATE task_interventions SET status = 'applied', applied_at = ?
                    WHERE workspace_id = ? AND task_id = ? AND status = 'pending'
                      AND kind = 'interrupt'""",
                    (paused_at, workspace_id, task_id),
                )
            normalized_reason = reason.strip() if reason else task["control_reason"]
            self._set_task_dispatch_locked(workspace_id, task_id, "blocked")
            self._record_task_transition_locked(
                workspace_id,
                task,
                expected_revision=expected_revision,
                event_type="task.paused",
                payload={"reason": normalized_reason, "interrupted_attempt_id": attempt_id},
                changes={
                    "status": "paused",
                    "control_reason": normalized_reason,
                },
                checkpoint_step_id=(
                    str(task["current_step_id"])
                    if task["current_step_id"] is not None
                    else None
                ),
                checkpoint_attempt_id=attempt_id,
                events_after=[
                    (
                        "task.intervention.applied",
                        {
                            "intervention_id": intervention["id"],
                            "kind": "interrupt",
                            "step_id": intervention["step_id"],
                        },
                    )
                    for intervention in interrupts
                ],
            )
        return self.get_task(workspace_id, task_id) or {}

    def resume_task(
        self,
        workspace_id: str,
        task_id: str,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        with self.lock, self.connection:
            task = self._get_task_locked(workspace_id, task_id, for_update=True)
            self._assert_task_revision(task, expected_revision)
            self._require_task_status(
                task,
                "resume",
                {"paused", "waiting_for_approval", "waiting_for_user"},
            )
            attempt_id: str | None = None
            if task["current_step_id"] is not None:
                step = self._active_task_step_locked(
                    workspace_id,
                    task,
                    str(task["current_step_id"]),
                )
                attempt = self._latest_task_attempt_locked(
                    workspace_id,
                    task_id,
                    str(step["id"]),
                )
                if step["status"] in {"waiting_for_approval", "waiting_for_user"}:
                    self.connection.execute(
                        """UPDATE task_steps SET status = 'running'
                        WHERE workspace_id = ? AND task_id = ? AND id = ?""",
                        (workspace_id, task_id, step["id"]),
                    )
                if attempt is not None and attempt["status"] in {
                    "waiting_for_approval",
                    "waiting_for_user",
                }:
                    attempt_id = str(attempt["id"])
                    self.connection.execute(
                        """UPDATE task_step_attempts SET status = 'running', updated_at = ?
                        WHERE workspace_id = ? AND task_id = ? AND id = ?""",
                        (utc_now(), workspace_id, task_id, attempt_id),
                    )
            self._set_task_dispatch_locked(workspace_id, task_id, "ready")
            self._record_task_transition_locked(
                workspace_id,
                task,
                expected_revision=expected_revision,
                event_type="task.resumed",
                payload={"step_id": task["current_step_id"]},
                changes={"status": "running", "control_reason": None},
                checkpoint_step_id=(
                    str(task["current_step_id"])
                    if task["current_step_id"] is not None
                    else None
                ),
                checkpoint_attempt_id=attempt_id,
            )
        return self.get_task(workspace_id, task_id) or {}

    def request_task_cancel(
        self,
        workspace_id: str,
        task_id: str,
        *,
        expected_revision: int,
        reason: str | None = None,
    ) -> dict[str, Any]:
        normalized_reason = reason.strip() if reason else None
        with self.lock, self.connection:
            task = self._get_task_locked(workspace_id, task_id, for_update=True)
            self._assert_task_revision(task, expected_revision)
            self._require_task_status(
                task,
                "request cancellation for",
                self._TASK_NONTERMINAL_STATUSES - {"cancel_requested"},
            )
            self._set_task_dispatch_locked(workspace_id, task_id, "blocked")
            self._record_task_transition_locked(
                workspace_id,
                task,
                expected_revision=expected_revision,
                event_type="task.cancel_requested",
                payload={"reason": normalized_reason},
                changes={
                    "status": "cancel_requested",
                    "control_reason": normalized_reason,
                },
                checkpoint_step_id=(
                    str(task["current_step_id"])
                    if task["current_step_id"] is not None
                    else None
                ),
            )
        return self.get_task(workspace_id, task_id) or {}

    def mark_task_cancelled(
        self,
        workspace_id: str,
        task_id: str,
        *,
        expected_revision: int,
        reason: str | None = None,
    ) -> dict[str, Any]:
        cancelled_at = utc_now()
        with self.lock, self.connection:
            task = self._get_task_locked(workspace_id, task_id, for_update=True)
            self._assert_task_revision(task, expected_revision)
            self._require_task_status(task, "mark cancelled", {"cancel_requested"})
            normalized_reason = reason.strip() if reason else task["control_reason"]
            self.connection.execute(
                """UPDATE task_step_attempts SET status = 'cancelled',
                    updated_at = ?, completed_at = ?
                WHERE workspace_id = ? AND task_id = ?
                  AND status NOT IN ('completed', 'failed', 'interrupted', 'cancelled')""",
                (cancelled_at, cancelled_at, workspace_id, task_id),
            )
            self.connection.execute(
                """UPDATE task_steps SET status = 'cancelled', completed_at = ?
                WHERE workspace_id = ? AND task_id = ?
                  AND status NOT IN ('completed', 'failed', 'skipped', 'cancelled')""",
                (cancelled_at, workspace_id, task_id),
            )
            pending_interventions = self._all(
                """SELECT * FROM task_interventions
                WHERE workspace_id = ? AND task_id = ? AND status = 'pending'
                ORDER BY created_at, id FOR UPDATE""",
                (workspace_id, task_id),
            )
            self.connection.execute(
                """UPDATE task_interventions SET status = 'rejected'
                WHERE workspace_id = ? AND task_id = ? AND status = 'pending'""",
                (workspace_id, task_id),
            )
            self._set_task_dispatch_locked(workspace_id, task_id, "terminal")
            self._record_task_transition_locked(
                workspace_id,
                task,
                expected_revision=expected_revision,
                event_type="task.cancelled",
                payload={"reason": normalized_reason},
                changes={
                    "status": "cancelled",
                    "control_reason": normalized_reason,
                    "completed_at": cancelled_at,
                },
                checkpoint_step_id=(
                    str(task["current_step_id"])
                    if task["current_step_id"] is not None
                    else None
                ),
                events_after=[
                    (
                        "task.intervention.rejected",
                        {
                            "intervention_id": intervention["id"],
                            "kind": intervention["kind"],
                            "step_id": intervention["step_id"],
                            "reason": "task_cancelled",
                        },
                    )
                    for intervention in pending_interventions
                ],
            )
        return self.get_task(workspace_id, task_id) or {}

    def suspend_task(
        self,
        workspace_id: str,
        task_id: str,
        step_id: str,
        *,
        expected_revision: int,
        reason: str,
        reference_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if reason not in {"approval", "user"}:
            raise ValueError("Task suspension reason must be approval or user")
        safe_payload = payload or {}
        self._validate_no_raw_secrets(safe_payload)
        next_status = "waiting_for_approval" if reason == "approval" else "waiting_for_user"
        now = utc_now()
        with self.lock, self.connection:
            task = self._get_task_locked(workspace_id, task_id, for_update=True)
            self._assert_task_revision(task, expected_revision)
            self._require_task_status(task, "suspend", {"running"})
            step = self._active_task_step_locked(workspace_id, task, step_id)
            if step["status"] != "running":
                raise RepositoryConflict("Only a running Task step can suspend")
            attempt = self._latest_task_attempt_locked(
                workspace_id,
                task_id,
                step_id,
            )
            if attempt is None or attempt["status"] != "running":
                raise RepositoryConflict("Task step has no running attempt")
            self.connection.execute(
                """UPDATE task_steps SET status = ?
                WHERE workspace_id = ? AND task_id = ? AND id = ?""",
                (next_status, workspace_id, task_id, step_id),
            )
            self.connection.execute(
                """UPDATE task_step_attempts SET status = ?, updated_at = ?
                WHERE workspace_id = ? AND task_id = ? AND id = ?""",
                (next_status, now, workspace_id, task_id, attempt["id"]),
            )
            self._set_task_dispatch_locked(workspace_id, task_id, "blocked")
            self._record_task_transition_locked(
                workspace_id,
                task,
                expected_revision=expected_revision,
                event_type=f"task.waiting_for_{reason}",
                payload={
                    "step_id": step_id,
                    "attempt_id": attempt["id"],
                    "reference_id": reference_id,
                    **safe_payload,
                },
                changes={"status": next_status},
                checkpoint_step_id=step_id,
                checkpoint_attempt_id=str(attempt["id"]),
            )
        return self.get_task(workspace_id, task_id) or {}

    def mark_task_waiting_for_approval(
        self,
        workspace_id: str,
        task_id: str,
        step_id: str,
        approval_id: str,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        return self.suspend_task(
            workspace_id,
            task_id,
            step_id,
            expected_revision=expected_revision,
            reason="approval",
            reference_id=approval_id,
        )

    def mark_task_waiting_for_user(
        self,
        workspace_id: str,
        task_id: str,
        step_id: str,
        *,
        expected_revision: int,
        prompt: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.suspend_task(
            workspace_id,
            task_id,
            step_id,
            expected_revision=expected_revision,
            reason="user",
            payload={"prompt": prompt or {}},
        )

    def retry_task_step(
        self,
        workspace_id: str,
        task_id: str,
        step_id: str,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        with self.lock, self.connection:
            task = self._get_task_locked(workspace_id, task_id, for_update=True)
            self._assert_task_revision(task, expected_revision)
            self._require_task_status(task, "retry a step for", {"waiting_for_user"})
            step = self._active_task_step_locked(workspace_id, task, step_id)
            if step["status"] != "failed":
                raise RepositoryConflict("Only a failed Task step can be retried")
            self.connection.execute(
                """UPDATE task_steps SET status = 'pending', completed_at = NULL,
                    output_json = NULL, evidence_json = '[]', error_json = NULL
                WHERE workspace_id = ? AND task_id = ? AND id = ?""",
                (workspace_id, task_id, step_id),
            )
            self._set_task_dispatch_locked(workspace_id, task_id, "ready")
            self._record_task_transition_locked(
                workspace_id,
                task,
                expected_revision=expected_revision,
                event_type="task.step.retry_requested",
                payload={
                    "step_id": step_id,
                    "next_attempt": int(step["attempt_count"]) + 1,
                },
                changes={
                    "status": "running",
                    "current_step_id": step_id,
                    "error_json": None,
                    "control_reason": None,
                },
                checkpoint_step_id=step_id,
            )
        return self.get_task(workspace_id, task_id) or {}

    def intervene_task(
        self,
        workspace_id: str,
        task_id: str,
        *,
        expected_revision: int,
        kind: str,
        content: str,
        step_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if kind not in {"queue", "steer", "interrupt"}:
            raise ValueError("Task intervention kind is invalid")
        normalized_content = content.strip()
        if not 1 <= len(normalized_content) <= 40_000:
            raise ValueError(
                "Task intervention content must contain between 1 and 40000 characters"
            )
        safe_payload = payload or {}
        self._validate_no_raw_secrets(safe_payload)
        created_at = utc_now()
        with self.lock, self.connection:
            task = self._get_task_locked(workspace_id, task_id, for_update=True)
            self._assert_task_revision(task, expected_revision)
            if kind == "queue":
                allowed = {
                    "running",
                    "waiting_for_approval",
                    "waiting_for_user",
                    "pause_requested",
                    "paused",
                }
            else:
                allowed = {"running", "waiting_for_approval", "waiting_for_user"}
            self._require_task_status(task, f"{kind}", allowed)
            effective_step_id = step_id or (
                str(task["current_step_id"])
                if task["current_step_id"] is not None
                else None
            )
            if effective_step_id is not None:
                self._active_task_step_locked(
                    workspace_id,
                    task,
                    effective_step_id,
                    for_update=False,
                )
            intervention_id = new_id("tint")
            self.connection.execute(
                """INSERT INTO task_interventions
                (id, workspace_id, task_id, step_id, kind, status, content,
                 payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
                (
                    intervention_id,
                    workspace_id,
                    task_id,
                    effective_step_id,
                    kind,
                    normalized_content,
                    self._json(safe_payload),
                    created_at,
                ),
            )
            changes: dict[str, Any] = {}
            if kind == "interrupt":
                changes = {
                    "status": "pause_requested",
                    "control_reason": "user_interrupt",
                }
                self._set_task_dispatch_locked(workspace_id, task_id, "blocked")
            event_suffix = {
                "queue": "queued",
                "steer": "steered",
                "interrupt": "interrupt_requested",
            }[kind]
            self._record_task_transition_locked(
                workspace_id,
                task,
                expected_revision=expected_revision,
                event_type=f"task.intervention.{event_suffix}",
                payload={
                    "intervention_id": intervention_id,
                    "kind": kind,
                    "step_id": effective_step_id,
                    "content": normalized_content,
                    "payload": safe_payload,
                },
                changes=changes,
                checkpoint_step_id=effective_step_id,
            )
        return self.get_task(workspace_id, task_id) or {}

    def list_task_interventions(
        self,
        workspace_id: str,
        task_id: str,
        *,
        pending_only: bool = False,
    ) -> list[dict[str, Any]]:
        clauses = ["workspace_id = ?", "task_id = ?"]
        values: list[Any] = [workspace_id, task_id]
        if pending_only:
            clauses.append("status = 'pending'")
        rows = self._all(
            f"""SELECT * FROM task_interventions
            WHERE {' AND '.join(clauses)} ORDER BY created_at, id""",
            tuple(values),
        )
        return [self._task_intervention_record(row) for row in rows]

    def apply_task_intervention(
        self,
        workspace_id: str,
        task_id: str,
        intervention_id: str,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        applied_at = utc_now()
        with self.lock, self.connection:
            task = self._get_task_locked(workspace_id, task_id, for_update=True)
            self._assert_task_revision(task, expected_revision)
            intervention = self._one(
                """SELECT * FROM task_interventions
                WHERE workspace_id = ? AND task_id = ? AND id = ? FOR UPDATE""",
                (workspace_id, task_id, intervention_id),
            )
            if intervention is None:
                raise RepositoryConflict("Task intervention does not exist")
            if intervention["status"] != "pending":
                raise RepositoryConflict("Task intervention is not pending")
            self.connection.execute(
                """UPDATE task_interventions SET status = 'applied', applied_at = ?
                WHERE workspace_id = ? AND task_id = ? AND id = ?""",
                (applied_at, workspace_id, task_id, intervention_id),
            )
            self._record_task_transition_locked(
                workspace_id,
                task,
                expected_revision=expected_revision,
                event_type="task.intervention.applied",
                payload={
                    "intervention_id": intervention_id,
                    "kind": intervention["kind"],
                    "step_id": intervention["step_id"],
                },
                checkpoint_step_id=(
                    str(intervention["step_id"])
                    if intervention["step_id"] is not None
                    else None
                ),
            )
        return self.get_task(workspace_id, task_id) or {}

    def link_task_run(
        self,
        workspace_id: str,
        task_id: str,
        step_id: str,
        attempt_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        created_at = utc_now()
        with self.lock, self.connection:
            task = self._get_task_locked(workspace_id, task_id, for_update=True)
            attempt = self._one(
                """SELECT id FROM task_step_attempts
                WHERE workspace_id = ? AND task_id = ? AND step_id = ? AND id = ?""",
                (workspace_id, task_id, step_id, attempt_id),
            )
            if attempt is None:
                raise RepositoryConflict("Task step attempt does not exist")
            run = self._one(
                """SELECT id, thread_id FROM runs
                WHERE workspace_id = ? AND id = ?""",
                (workspace_id, run_id),
            )
            if run is None or str(run["thread_id"]) != str(task["thread_id"]):
                raise RepositoryConflict("Run does not belong to the Task Thread")
            existing = self._one(
                """SELECT * FROM task_run_links
                WHERE workspace_id = ? AND attempt_id = ?""",
                (workspace_id, attempt_id),
            )
            if existing is not None:
                if str(existing["run_id"]) != run_id:
                    raise RepositoryConflict("Task attempt is already linked to another Run")
                return dict(existing)
            self.connection.execute(
                """INSERT INTO task_run_links
                (workspace_id, task_id, step_id, attempt_id, run_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    workspace_id,
                    task_id,
                    step_id,
                    attempt_id,
                    run_id,
                    created_at,
                ),
            )
        return {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "step_id": step_id,
            "attempt_id": attempt_id,
            "run_id": run_id,
            "created_at": created_at,
        }

    def get_task_run_link(
        self,
        workspace_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        row = self._one(
            """SELECT * FROM task_run_links
            WHERE workspace_id = ? AND run_id = ?""",
            (workspace_id, run_id),
        )
        return dict(row) if row else None

    def list_task_events(
        self,
        workspace_id: str,
        task_id: str,
        *,
        after: int = 0,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        if after < 0:
            raise ValueError("Task event cursor cannot be negative")
        if not 1 <= limit <= 1000:
            raise ValueError("Task event limit must be between 1 and 1000")
        rows = self._all(
            """SELECT * FROM task_events
            WHERE workspace_id = ? AND task_id = ? AND sequence > ?
            ORDER BY sequence LIMIT ?""",
            (workspace_id, task_id, after, limit),
        )
        return [
            {
                "id": row["id"],
                "workspace_id": row["workspace_id"],
                "task_id": row["task_id"],
                "sequence": int(row["sequence"]),
                "type": row["type"],
                "timestamp": row["created_at"],
                "step_id": self._decoded_json(row["payload_json"]).get("step_id"),
                "attempt_id": self._decoded_json(row["payload_json"]).get(
                    "attempt_id"
                ),
                "payload": self._decoded_json(row["payload_json"]),
            }
            for row in rows
        ]

    def list_task_dispatches(
        self,
        workspace_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if status is not None and status not in {"ready", "claimed", "blocked", "terminal"}:
            raise ValueError("Task dispatch status is invalid")
        if not 1 <= limit <= 500:
            raise ValueError("Task dispatch limit must be between 1 and 500")
        if status is None:
            rows = self._all(
                """SELECT * FROM task_dispatch WHERE workspace_id = ?
                ORDER BY available_at, task_id LIMIT ?""",
                (workspace_id, limit),
            )
        else:
            rows = self._all(
                """SELECT * FROM task_dispatch WHERE workspace_id = ? AND status = ?
                ORDER BY available_at, task_id LIMIT ?""",
                (workspace_id, status, limit),
            )
        return [dict(row) for row in rows]

    def recover_nonterminal_tasks(self) -> list[dict[str, Any]]:
        """Move interrupted active Tasks to a safe explicit control boundary.

        Completed steps remain committed. An in-flight attempt is marked interrupted and
        its step returns to pending; the runtime never silently replays it after restart.
        """
        recovered: list[dict[str, Any]] = []
        recovered_at = utc_now()
        with self.lock, self.connection:
            rows = self._all(
                """SELECT * FROM tasks
                WHERE status IN ('running', 'pause_requested', 'cancel_requested')
                ORDER BY created_at, id FOR UPDATE"""
            )
            for task in rows:
                workspace_id = str(task["workspace_id"])
                task_id = str(task["id"])
                current_status = str(task["status"])
                dispatch = self._one(
                    """SELECT * FROM task_dispatch
                    WHERE workspace_id = ? AND task_id = ? FOR UPDATE""",
                    (workspace_id, task_id),
                )
                if (
                    dispatch is not None
                    and dispatch["status"] == "claimed"
                    and str(dispatch["lease_expires_at"] or "") > recovered_at
                ):
                    # Another live API worker owns this Task. Startup recovery must
                    # not pause or rewrite work protected by an unexpired lease.
                    continue
                attempt = (
                    self._latest_task_attempt_locked(
                        workspace_id,
                        task_id,
                        str(task["current_step_id"]),
                    )
                    if task["current_step_id"] is not None
                    else None
                )
                linked_run = None
                if attempt is not None:
                    linked_run = self._one(
                        """SELECT r.* FROM task_run_links l
                        JOIN runs r
                          ON r.workspace_id = l.workspace_id AND r.id = l.run_id
                        WHERE l.workspace_id = ? AND l.attempt_id = ?""",
                        (workspace_id, attempt["id"]),
                    )
                if linked_run is not None and linked_run["status"] in {
                    "completed",
                    "failed",
                    "cancelled",
                    "waiting_for_approval",
                }:
                    # A Run boundary is already durable. Reconcile it instead of
                    # interrupting the Attempt and creating a second Run.
                    if linked_run["status"] == "waiting_for_approval":
                        decided = self._one(
                            """SELECT * FROM approvals
                            WHERE workspace_id = ? AND run_id = ? AND status <> 'pending'
                            ORDER BY decided_at DESC NULLS LAST LIMIT 1""",
                            (workspace_id, linked_run["id"]),
                        )
                        if decided is not None:
                            sequence_row = self._one(
                                """UPDATE runs SET status = 'failed', completed_at = ?,
                                    next_event_sequence = next_event_sequence + 1
                                WHERE workspace_id = ? AND id = ?
                                RETURNING next_event_sequence AS value""",
                                (recovered_at, workspace_id, linked_run["id"]),
                            )
                            if sequence_row is None:
                                raise RepositoryConflict(
                                    "Approval Run disappeared during Task recovery"
                                )
                            self.connection.execute(
                                """INSERT INTO events
                                (id, workspace_id, run_id, sequence, type, payload_json, created_at)
                                VALUES (?, ?, ?, ?, 'run.failed', ?, ?)""",
                                (
                                    new_id("evt"),
                                    workspace_id,
                                    linked_run["id"],
                                    int(sequence_row["value"]),
                                    self._json(
                                        {
                                            "code": "approval_result_verification_required",
                                            "message": (
                                                "The approval was decided before restart, but "
                                                "the external result is not durable. Verify the "
                                                "target system before retrying."
                                            ),
                                            "automatic_retry": False,
                                            "external_tool_results": "verification_required",
                                        }
                                    ),
                                    recovered_at,
                                ),
                            )
                    self._set_task_dispatch_locked(workspace_id, task_id, "ready")
                    reconciled = self._hydrate_task_locked(workspace_id, task_id) or {}
                    reconciled["previous_status"] = current_status
                    reconciled["recovery_action"] = "reconcile_linked_run"
                    recovered.append(reconciled)
                    continue
                if current_status == "cancel_requested":
                    self.connection.execute(
                        """UPDATE task_step_attempts SET status = 'cancelled',
                            updated_at = ?, completed_at = ?
                        WHERE workspace_id = ? AND task_id = ?
                          AND status NOT IN ('completed', 'failed', 'interrupted', 'cancelled')""",
                        (recovered_at, recovered_at, workspace_id, task_id),
                    )
                    self.connection.execute(
                        """UPDATE task_steps SET status = 'cancelled', completed_at = ?
                        WHERE workspace_id = ? AND task_id = ?
                          AND status NOT IN ('completed', 'failed', 'skipped', 'cancelled')""",
                        (recovered_at, workspace_id, task_id),
                    )
                    self._set_task_dispatch_locked(workspace_id, task_id, "terminal")
                    updated, event, _checkpoint = self._record_task_transition_locked(
                        workspace_id,
                        task,
                        expected_revision=int(task["revision"]),
                        event_type="task.cancelled",
                        payload={"reason": "runtime_restarted"},
                        changes={
                            "status": "cancelled",
                            "control_reason": "runtime_restarted",
                            "completed_at": recovered_at,
                        },
                        checkpoint_step_id=(
                            str(task["current_step_id"])
                            if task["current_step_id"] is not None
                            else None
                        ),
                    )
                else:
                    attempt_id = self._interrupt_active_attempt_locked(
                        workspace_id,
                        task,
                        timestamp=recovered_at,
                    )
                    self._set_task_dispatch_locked(workspace_id, task_id, "blocked")
                    if current_status == "running":
                        task, _requested_event, _requested_checkpoint = (
                            self._record_task_transition_locked(
                                workspace_id,
                                task,
                                expected_revision=int(task["revision"]),
                                event_type="task.pause_requested",
                                payload={"reason": "runtime_restarted"},
                                changes={
                                    "status": "pause_requested",
                                    "control_reason": "runtime_restarted",
                                },
                                checkpoint_step_id=(
                                    str(task["current_step_id"])
                                    if task["current_step_id"] is not None
                                    else None
                                ),
                                checkpoint_attempt_id=attempt_id,
                            )
                        )
                    updated, event, _checkpoint = self._record_task_transition_locked(
                        workspace_id,
                        task,
                        expected_revision=int(task["revision"]),
                        event_type="task.paused",
                        payload={
                            "reason": "runtime_restarted",
                            "automatic_retry": False,
                            "interrupted_attempt_id": attempt_id,
                        },
                        changes={
                            "status": "paused",
                            "control_reason": "runtime_restarted",
                        },
                        checkpoint_step_id=(
                            str(task["current_step_id"])
                            if task["current_step_id"] is not None
                            else None
                        ),
                        checkpoint_attempt_id=attempt_id,
                    )
                recovered.append(
                    {
                        "workspace_id": workspace_id,
                        "task_id": task_id,
                        "previous_status": current_status,
                        "status": updated["status"],
                        "revision": int(updated["revision"]),
                        "event_id": event["id"],
                    }
                )
        return recovered
