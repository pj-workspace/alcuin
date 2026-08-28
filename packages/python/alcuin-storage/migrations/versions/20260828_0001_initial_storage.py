"""Create the initial Workspace-scoped Alcuin storage schema."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260828_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_table(
        "agents",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("current_version_id", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "slug",
            name="agents_workspace_id_slug_key",
        ),
    )
    op.create_table(
        "agent_versions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("agent_id", sa.Text(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "agent_id",
            "version",
            name="agent_versions_agent_id_version_key",
        ),
    )
    op.create_table(
        "threads",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("agent_id", sa.Text(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("context_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("thread_id", sa.Text(), sa.ForeignKey("threads.id"), nullable=False),
        sa.Column(
            "agent_version_id",
            sa.Text(),
            sa.ForeignKey("agent_versions.id"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column(
            "next_event_sequence",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.Text()),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("run_id", sa.Text(), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("run_id", "sequence", name="events_run_id_sequence_key"),
    )
    op.create_table(
        "approvals",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("run_id", sa.Text(), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.Text()),
    )
    op.create_table(
        "extensions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("manifest_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("health", sa.Text(), nullable=False),
        sa.Column("manifest_json", sa.Text(), nullable=False),
        sa.Column("credential_refs_json", sa.Text(), nullable=False),
        sa.Column("installed_at", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "manifest_id",
            name="extensions_workspace_id_manifest_id_key",
        ),
    )
    op.create_table(
        "knowledge_sources",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "name",
            name="knowledge_sources_workspace_id_name_key",
        ),
    )
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column(
            "source_id",
            sa.Text(),
            sa.ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_uri", sa.Text()),
        sa.Column("content", sa.Text()),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("index_revision", sa.Text(), nullable=False, server_default=""),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "source_id",
            "content_hash",
            name="knowledge_documents_workspace_source_hash_key",
        ),
    )
    op.create_index(
        "idx_knowledge_sources_workspace",
        "knowledge_sources",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "idx_knowledge_documents_source",
        "knowledge_documents",
        ["workspace_id", "source_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_knowledge_documents_source", table_name="knowledge_documents")
    op.drop_index("idx_knowledge_sources_workspace", table_name="knowledge_sources")
    for table in (
        "knowledge_documents",
        "knowledge_sources",
        "extensions",
        "approvals",
        "events",
        "runs",
        "threads",
        "agent_versions",
        "agents",
        "workspaces",
    ):
        op.drop_table(table)
