"""Durable question/answer boundaries and private runtime continuations."""
from alembic import op
import sqlalchemy as sa

revision = "20260922_0012"
down_revision = "20260912_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_inputs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.Text(), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("question_json", sa.Text(), nullable=False),
        sa.Column("answer_json", sa.Text()),
        sa.Column("continuation_json", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("answered_at", sa.Text()),
        sa.CheckConstraint("status IN ('pending', 'answered', 'skipped')", name="run_inputs_status_check"),
        sa.UniqueConstraint("workspace_id", "id", name="run_inputs_workspace_id_key"),
    )
    op.create_index("run_inputs_one_pending", "run_inputs", ["run_id"], unique=True, postgresql_where=sa.text("status = 'pending'"))
    op.create_index("run_inputs_workspace_run", "run_inputs", ["workspace_id", "run_id"])
    op.drop_index("runs_one_active_per_thread_key", table_name="runs")
    op.create_index("runs_one_active_per_thread_key", "runs", ["thread_id"], unique=True, postgresql_where=sa.text("status IN ('queued', 'running', 'waiting_for_approval', 'waiting_for_input')"))


def downgrade() -> None:
    # Never silently orphan an unanswered interaction by removing its schema.
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT 1 FROM runs WHERE status = 'waiting_for_input' LIMIT 1")).first():
        raise RuntimeError("Resolve waiting input Runs before downgrading")
    op.drop_index("runs_one_active_per_thread_key", table_name="runs")
    op.create_index("runs_one_active_per_thread_key", "runs", ["thread_id"], unique=True, postgresql_where=sa.text("status IN ('queued', 'running', 'waiting_for_approval')"))
    op.drop_table("run_inputs")
