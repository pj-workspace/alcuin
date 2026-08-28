"""SQLite persistence adapter for local Alcuin development and verification."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from alcuin_core.contracts import (
    AgentCreate,
    AgentDefinition,
    ExtensionManifest,
    KnowledgeSourceCreate,
    utc_now,
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


class SqliteStore:
    """SQLite repository with Workspace scoping enforced in every resource lookup."""

    def __init__(self, database_path: str) -> None:
        if database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.lock = threading.RLock()
        self.initialize()

    def initialize(self) -> None:
        with self.lock, self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workspaces (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
                    slug TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_version_id TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(workspace_id, slug)
                );
                CREATE TABLE IF NOT EXISTS agent_versions (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
                    agent_id TEXT NOT NULL REFERENCES agents(id),
                    version INTEGER NOT NULL,
                    definition_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(agent_id, version)
                );
                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
                    agent_id TEXT NOT NULL REFERENCES agents(id),
                    title TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
                    thread_id TEXT NOT NULL REFERENCES threads(id),
                    agent_version_id TEXT NOT NULL REFERENCES agent_versions(id),
                    status TEXT NOT NULL,
                    input TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    sequence INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    note TEXT,
                    created_at TEXT NOT NULL,
                    decided_at TEXT
                );
                CREATE TABLE IF NOT EXISTS extensions (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
                    manifest_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    health TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    credential_refs_json TEXT NOT NULL,
                    installed_at TEXT NOT NULL,
                    UNIQUE(workspace_id, manifest_id)
                );
                CREATE TABLE IF NOT EXISTS knowledge_sources (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workspace_id, name)
                );
                CREATE TABLE IF NOT EXISTS knowledge_documents (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
                    source_id TEXT NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    source_uri TEXT,
                    content TEXT,
                    content_hash TEXT NOT NULL,
                    index_revision TEXT NOT NULL DEFAULT '',
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workspace_id, source_id, content_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_sources_workspace
                    ON knowledge_sources(workspace_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_knowledge_documents_source
                    ON knowledge_documents(workspace_id, source_id, created_at);
                """
            )
            document_columns = {
                row["name"]
                for row in self.connection.execute(
                    "PRAGMA table_info(knowledge_documents)"
                ).fetchall()
            }
            if "content" not in document_columns:
                self.connection.execute(
                    "ALTER TABLE knowledge_documents ADD COLUMN content TEXT"
                )
            if "index_revision" not in document_columns:
                self.connection.execute(
                    "ALTER TABLE knowledge_documents "
                    "ADD COLUMN index_revision TEXT NOT NULL DEFAULT ''"
                )
        self.seed_starter()

    def close(self) -> None:
        self.connection.close()

    def _one(self, query: str, params: tuple[Any, ...]) -> sqlite3.Row | None:
        return self.connection.execute(query, params).fetchone()

    def _all(self, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return list(self.connection.execute(query, params).fetchall())

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _agent(
        row: sqlite3.Row, definition: dict[str, Any] | None = None
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
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO workspaces(id, name, created_at) VALUES (?, ?, ?)",
                ("ws_demo", "Alcuin Workspace", created_at),
            )
            self.connection.execute(
                """INSERT OR IGNORE INTO agents
                (id, workspace_id, slug, name, description, status, current_version_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "agt_starter",
                    "ws_demo",
                    "alcuin-starter",
                    definition.identity.name,
                    definition.identity.description,
                    "published",
                    "av_starter_1",
                    created_at,
                ),
            )
            self.connection.execute(
                """INSERT OR IGNORE INTO agent_versions
                (id, workspace_id, agent_id, version, definition_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    "av_starter_1",
                    "ws_demo",
                    "agt_starter",
                    1,
                    definition.model_dump_json(),
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

    def create_agent(self, workspace_id: str, payload: AgentCreate) -> dict[str, Any]:
        agent_id, version_id, created_at = new_id("agt"), new_id("av"), utc_now()
        try:
            with self.lock, self.connection:
                self.connection.execute(
                    """INSERT INTO agents
                    (id, workspace_id, slug, name, description, status, current_version_id, created_at)
                    VALUES (?, ?, ?, ?, ?, 'draft', ?, ?)""",
                    (
                        agent_id,
                        workspace_id,
                        payload.slug,
                        payload.definition.identity.name,
                        payload.definition.identity.description,
                        version_id,
                        created_at,
                    ),
                )
                self.connection.execute(
                    """INSERT INTO agent_versions
                    (id, workspace_id, agent_id, version, definition_json, created_at)
                    VALUES (?, ?, ?, 1, ?, ?)""",
                    (
                        version_id,
                        workspace_id,
                        agent_id,
                        payload.definition.model_dump_json(),
                        created_at,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if "agents.workspace_id, agents.slug" in str(exc):
                raise ValueError("Agent slug already exists in this workspace") from exc
            raise
        return self.get_agent(workspace_id, agent_id) or {}

    def create_agent_version(
        self, workspace_id: str, agent_id: str, definition: AgentDefinition
    ) -> dict[str, Any] | None:
        agent = self.get_agent(workspace_id, agent_id)
        if not agent:
            return None
        row = self._one(
            "SELECT COALESCE(MAX(version), 0) AS value FROM agent_versions WHERE agent_id = ?",
            (agent_id,),
        )
        version = int(row["value"]) + 1
        version_id = new_id("av")
        with self.lock, self.connection:
            self.connection.execute(
                """INSERT INTO agent_versions
                (id, workspace_id, agent_id, version, definition_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    version_id,
                    workspace_id,
                    agent_id,
                    version,
                    definition.model_dump_json(),
                    utc_now(),
                ),
            )
            self.connection.execute(
                """UPDATE agents SET current_version_id = ?, status = 'draft', name = ?, description = ?
                WHERE id = ? AND workspace_id = ?""",
                (
                    version_id,
                    definition.identity.name,
                    definition.identity.description,
                    agent_id,
                    workspace_id,
                ),
            )
        return self.get_agent(workspace_id, agent_id)

    def publish_agent(self, workspace_id: str, agent_id: str) -> dict[str, Any] | None:
        with self.lock, self.connection:
            cursor = self.connection.execute(
                "UPDATE agents SET status = 'published' WHERE id = ? AND workspace_id = ?",
                (agent_id, workspace_id),
            )
        return self.get_agent(workspace_id, agent_id) if cursor.rowcount else None

    def create_thread(
        self, workspace_id: str, agent_id: str, title: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        thread_id = new_id("thr")
        with self.lock, self.connection:
            self.connection.execute(
                """INSERT INTO threads(id, workspace_id, agent_id, title, context_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    thread_id,
                    workspace_id,
                    agent_id,
                    title,
                    self._json(context),
                    utc_now(),
                ),
            )
        return self.get_thread(workspace_id, thread_id) or {}

    def get_thread(self, workspace_id: str, thread_id: str) -> dict[str, Any] | None:
        row = self._one(
            "SELECT * FROM threads WHERE workspace_id = ? AND id = ?",
            (workspace_id, thread_id),
        )
        if not row:
            return None
        result = dict(row)
        result["context"] = json.loads(result.pop("context_json"))
        return result

    def list_threads(self, workspace_id: str) -> list[dict[str, Any]]:
        rows = self._all(
            "SELECT * FROM threads WHERE workspace_id = ? ORDER BY created_at DESC",
            (workspace_id,),
        )
        result = []
        for row in rows:
            item = dict(row)
            item["context"] = json.loads(item.pop("context_json"))
            result.append(item)
        return result

    def create_run(
        self, workspace_id: str, thread_id: str, agent_version_id: str, prompt: str
    ) -> dict[str, Any]:
        run_id = new_id("run")
        with self.lock, self.connection:
            self.connection.execute(
                """INSERT INTO runs
                (id, workspace_id, thread_id, agent_version_id, status, input, created_at)
                VALUES (?, ?, ?, ?, 'queued', ?, ?)""",
                (run_id, workspace_id, thread_id, agent_version_id, prompt, utc_now()),
            )
        return self.get_run(workspace_id, run_id) or {}

    def get_run(self, workspace_id: str, run_id: str) -> dict[str, Any] | None:
        row = self._one(
            "SELECT * FROM runs WHERE workspace_id = ? AND id = ?",
            (workspace_id, run_id),
        )
        return dict(row) if row else None

    def list_runs(self, workspace_id: str, limit: int = 30) -> list[dict[str, Any]]:
        rows = self._all(
            """SELECT r.*, t.title, a.name AS agent_name
            FROM runs r JOIN threads t ON t.id = r.thread_id
            JOIN agent_versions v ON v.id = r.agent_version_id
            JOIN agents a ON a.id = v.agent_id
            WHERE r.workspace_id = ? ORDER BY r.created_at DESC LIMIT ?""",
            (workspace_id, limit),
        )
        return [dict(row) for row in rows]

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
                "SELECT COALESCE(MAX(sequence), 0) AS value FROM events WHERE run_id = ?",
                (run_id,),
            )
            sequence = int(row["value"]) + 1
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
    def _extension(row: sqlite3.Row) -> dict[str, Any]:
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
    def _knowledge_document(row: sqlite3.Row | None) -> dict[str, Any]:
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
