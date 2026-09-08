"""Pin Threads to immutable Agent Versions and publish exact versions."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260829_0003"
down_revision = "20260829_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("published_version_id", sa.Text()))
    op.add_column("agents", sa.Column("updated_at", sa.Text()))
    op.add_column("agent_versions", sa.Column("published_at", sa.Text()))
    op.add_column("agent_versions", sa.Column("definition_sha256", sa.Text()))
    op.add_column("threads", sa.Column("agent_version_id", sa.Text()))

    op.execute("UPDATE agents SET updated_at = created_at WHERE updated_at IS NULL")
    op.execute(
        """
        UPDATE agent_versions
        SET definition_sha256 = encode(
            sha256(convert_to(definition_json, 'UTF8')),
            'hex'
        )
        WHERE definition_sha256 IS NULL
        """
    )
    op.execute(
        """
        UPDATE agent_versions v
        SET published_at = v.created_at
        FROM agents a
        WHERE a.current_version_id = v.id
          AND a.status = 'published'
          AND v.published_at IS NULL
        """
    )
    op.execute(
        """
        UPDATE agents
        SET published_version_id = current_version_id
        WHERE status = 'published' AND published_version_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE threads t
        SET agent_version_id = COALESCE(
            (
                SELECT r.agent_version_id
                FROM runs r
                WHERE r.thread_id = t.id AND r.workspace_id = t.workspace_id
                ORDER BY r.created_at DESC, r.id DESC
                LIMIT 1
            ),
            (
                SELECT COALESCE(a.published_version_id, a.current_version_id)
                FROM agents a
                WHERE a.id = t.agent_id AND a.workspace_id = t.workspace_id
            )
        )
        WHERE t.agent_version_id IS NULL
        """
    )

    op.alter_column("agents", "updated_at", nullable=False)
    op.alter_column("agent_versions", "definition_sha256", nullable=False)
    op.alter_column("threads", "agent_version_id", nullable=False)

    op.create_check_constraint(
        "agent_versions_definition_sha256_check",
        "agent_versions",
        "char_length(definition_sha256) = 64",
    )
    op.create_foreign_key(
        "agents_current_version_id_fkey",
        "agents",
        "agent_versions",
        ["current_version_id"],
        ["id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "agents_published_version_id_fkey",
        "agents",
        "agent_versions",
        ["published_version_id"],
        ["id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "threads_agent_version_id_fkey",
        "threads",
        "agent_versions",
        ["agent_version_id"],
        ["id"],
    )
    op.create_index(
        "idx_agent_versions_workspace_agent_version",
        "agent_versions",
        ["workspace_id", "agent_id", "version"],
    )
    op.create_index(
        "idx_threads_workspace_agent_version",
        "threads",
        ["workspace_id", "agent_version_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_threads_workspace_agent_version", table_name="threads")
    op.drop_index(
        "idx_agent_versions_workspace_agent_version",
        table_name="agent_versions",
    )
    op.drop_constraint(
        "threads_agent_version_id_fkey",
        "threads",
        type_="foreignkey",
    )
    op.drop_constraint(
        "agents_published_version_id_fkey",
        "agents",
        type_="foreignkey",
    )
    op.drop_constraint(
        "agents_current_version_id_fkey",
        "agents",
        type_="foreignkey",
    )
    op.drop_constraint(
        "agent_versions_definition_sha256_check",
        "agent_versions",
        type_="check",
    )
    op.drop_column("threads", "agent_version_id")
    op.drop_column("agent_versions", "definition_sha256")
    op.drop_column("agent_versions", "published_at")
    op.drop_column("agents", "updated_at")
    op.drop_column("agents", "published_version_id")
