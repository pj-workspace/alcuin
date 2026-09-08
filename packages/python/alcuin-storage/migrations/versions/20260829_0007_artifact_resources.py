"""Add Workspace-scoped editable Artifact resources and immutable revisions."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260829_0007"
down_revision = "20260829_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "runs_id_thread_workspace_key",
        "runs",
        ["id", "thread_id", "workspace_id"],
    )
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("source_run_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id", "workspace_id"],
            ["threads.id", "threads.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id", "thread_id", "workspace_id"],
            ["runs.id", "runs.thread_id", "runs.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "char_length(btrim(title)) BETWEEN 1 AND 200",
            name="artifacts_title_length_check",
        ),
        sa.CheckConstraint(
            "kind ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$'",
            name="artifacts_kind_check",
        ),
        sa.CheckConstraint(
            "char_length(content) <= 500000",
            name="artifacts_content_length_check",
        ),
        sa.CheckConstraint("version > 0", name="artifacts_version_check"),
        sa.CheckConstraint(
            "content_type IN ('text/markdown', 'text/plain', 'application/json')",
            name="artifacts_content_type_check",
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="artifacts_content_sha256_check",
        ),
        sa.UniqueConstraint(
            "id",
            "workspace_id",
            name="artifacts_id_workspace_key",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "source_run_id",
            name="artifacts_workspace_run_key",
        ),
    )
    op.create_index(
        "idx_artifacts_workspace_thread_updated",
        "artifacts",
        ["workspace_id", "thread_id", "updated_at"],
    )
    op.create_table(
        "artifact_versions",
        sa.Column("artifact_id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_run_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["artifact_id", "workspace_id"],
            ["artifacts.id", "artifacts.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id", "workspace_id"],
            ["runs.id", "runs.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("version > 0", name="artifact_versions_version_check"),
        sa.CheckConstraint(
            "char_length(btrim(title)) BETWEEN 1 AND 200",
            name="artifact_versions_title_length_check",
        ),
        sa.CheckConstraint(
            "kind ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$'",
            name="artifact_versions_kind_check",
        ),
        sa.CheckConstraint(
            "char_length(content) <= 500000",
            name="artifact_versions_content_length_check",
        ),
        sa.CheckConstraint(
            "content_type IN ('text/markdown', 'text/plain', 'application/json')",
            name="artifact_versions_content_type_check",
        ),
        sa.CheckConstraint(
            "source IN ('runtime', 'user')",
            name="artifact_versions_source_check",
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="artifact_versions_sha256_check",
        ),
        sa.PrimaryKeyConstraint(
            "artifact_id",
            "version",
            name="artifact_versions_pkey",
        ),
    )
    op.create_index(
        "idx_artifact_versions_workspace_artifact",
        "artifact_versions",
        ["workspace_id", "artifact_id", "version"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_artifact_versions_workspace_artifact",
        table_name="artifact_versions",
    )
    op.drop_table("artifact_versions")
    op.drop_index(
        "idx_artifacts_workspace_thread_updated",
        table_name="artifacts",
    )
    op.drop_table("artifacts")
    op.drop_constraint("runs_id_thread_workspace_key", "runs", type_="unique")
