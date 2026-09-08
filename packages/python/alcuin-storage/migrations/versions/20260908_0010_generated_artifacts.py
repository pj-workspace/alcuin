"""Support HTML and independent deliverables from a single Run."""

from alembic import op
import sqlalchemy as sa

revision = "20260908_0010"
down_revision = "20260908_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "artifacts",
        sa.Column("generation_key", sa.Text(), nullable=False, server_default="main"),
    )
    op.drop_constraint("artifacts_workspace_run_key", "artifacts", type_="unique")
    op.create_unique_constraint(
        "artifacts_workspace_run_output_key",
        "artifacts",
        ["workspace_id", "source_run_id", "generation_key"],
    )
    op.create_check_constraint(
        "artifacts_generation_key_check",
        "artifacts",
        "generation_key ~ '^[A-Za-z0-9._-]{1,80}$'",
    )
    for table in ("artifacts", "artifact_versions"):
        op.drop_constraint(f"{table}_content_type_check", table, type_="check")
        op.create_check_constraint(
            f"{table}_content_type_check",
            table,
            "content_type IN ('text/markdown', 'text/plain', 'application/json', 'text/html')",
        )


def downgrade() -> None:
    connection = op.get_bind()
    # Never discard generated documents just to force an older schema.
    if connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM artifact_versions WHERE content_type = 'text/html') OR EXISTS (SELECT 1 FROM artifacts GROUP BY workspace_id, source_run_id HAVING COUNT(*) > 1)"
        )
    ).scalar():
        raise RuntimeError(
            "Cannot downgrade while HTML or multi-output artifacts exist; retain or export them before choosing a migration policy"
        )
    for table in ("artifacts", "artifact_versions"):
        op.drop_constraint(f"{table}_content_type_check", table, type_="check")
        op.create_check_constraint(
            f"{table}_content_type_check",
            table,
            "content_type IN ('text/markdown', 'text/plain', 'application/json')",
        )
    op.drop_constraint(
        "artifacts_workspace_run_output_key", "artifacts", type_="unique"
    )
    op.create_unique_constraint(
        "artifacts_workspace_run_key", "artifacts", ["workspace_id", "source_run_id"]
    )
    op.drop_constraint("artifacts_generation_key_check", "artifacts", type_="check")
    op.drop_column("artifacts", "generation_key")
