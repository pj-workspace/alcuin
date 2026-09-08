"""Freeze mutable customization inputs when a Run is accepted."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260829_0005"
down_revision = "20260829_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "runs_id_workspace_key",
        "runs",
        ["id", "workspace_id"],
    )
    op.create_table(
        "run_customization_snapshots",
        sa.Column("run_id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("snapshot_sha256", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            ["runs.id", "runs.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "char_length(snapshot_sha256) = 64",
            name="run_customization_snapshots_sha256_check",
        ),
        sa.UniqueConstraint(
            "run_id",
            "workspace_id",
            name="run_customization_snapshots_run_workspace_key",
        ),
    )
    op.create_index(
        "idx_run_customization_snapshots_workspace",
        "run_customization_snapshots",
        ["workspace_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_run_customization_snapshots_workspace",
        table_name="run_customization_snapshots",
    )
    op.drop_table("run_customization_snapshots")
    op.drop_constraint("runs_id_workspace_key", "runs", type_="unique")
