"""Database-neutral SQL repository behavior used by the PostgreSQL adapter."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from typing import Any

from alcuin_core.contracts import (
    AgentCreate,
    AgentDefinition,
    ExtensionManifest,
    KnowledgeSourceCreate,
    utc_now,
)

from .errors import RepositoryConflict


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


class SqlRepository:
    """Shared SQL behavior; concrete adapters own connections and migrations."""

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
    def _agent(
        row: Mapping[str, Any], definition: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        result = dict(row)
        if definition is not None:
            result["definition"] = definition
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

    def create_agent(self, workspace_id: str, payload: AgentCreate) -> dict[str, Any]:
        agent_id, version_id, created_at = new_id("agt"), new_id("av"), utc_now()
        definition_json, definition_sha256 = self._definition_snapshot(
            payload.definition
        )
        try:
            with self.lock, self.connection:
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
        definition_json, definition_sha256 = self._definition_snapshot(definition)
        with self.lock, self.connection:
            agent = self._one(
                """SELECT id FROM agents
                WHERE workspace_id = ? AND id = ? FOR UPDATE""",
                (workspace_id, agent_id),
            )
            if agent is None:
                return None
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

    def create_run(
        self,
        workspace_id: str,
        thread_id: str,
        agent_version_id: str,
        prompt: str,
        *,
        message_parts: list[dict[str, Any]] | None = None,
        estimated_tokens: int | None = None,
    ) -> dict[str, Any]:
        run_id, message_id, created_at = new_id("run"), new_id("msg"), utc_now()
        parts = message_parts or ([{"type": "text", "text": prompt}] if prompt else [])
        self._validate_persisted_parts(parts)
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
        return self.get_run(workspace_id, run_id) or {}

    def create_run_with_messages(
        self,
        workspace_id: str,
        thread_id: str,
        agent_version_id: str,
        prompt: str,
        parts: list[dict[str, Any]],
        estimated_tokens: int,
    ) -> dict[str, Any]:
        """Explicit conversation-kernel entrypoint used by API composition."""
        return self.create_run(
            workspace_id,
            thread_id,
            agent_version_id,
            prompt,
            message_parts=parts,
            estimated_tokens=estimated_tokens,
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
