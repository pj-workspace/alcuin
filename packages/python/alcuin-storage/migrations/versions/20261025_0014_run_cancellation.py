"""Preserve cancelled output and reserve Threads until cancellation completes."""
from alembic import op
import sqlalchemy as sa

revision = "20261025_0014"
down_revision = "20261025_0013"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("messages_status_check", "messages", type_="check")
    op.create_check_constraint("messages_status_check", "messages", "status IN ('pending', 'streaming', 'completed', 'failed', 'cancelled')")
    op.drop_index("runs_one_active_per_thread_key", table_name="runs")
    op.create_index("runs_one_active_per_thread_key", "runs", ["thread_id"], unique=True, postgresql_where=sa.text("status IN ('queued', 'running', 'waiting_for_approval', 'waiting_for_input', 'cancel_requested')"))


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT 1 FROM runs WHERE status = 'cancel_requested' UNION ALL SELECT 1 FROM messages WHERE status = 'cancelled' LIMIT 1")).first():
        raise RuntimeError("Resolve cancellation requests and preserve cancelled messages before downgrading")
    op.drop_index("runs_one_active_per_thread_key", table_name="runs")
    op.create_index("runs_one_active_per_thread_key", "runs", ["thread_id"], unique=True, postgresql_where=sa.text("status IN ('queued', 'running', 'waiting_for_approval', 'waiting_for_input')"))
    op.drop_constraint("messages_status_check", "messages", type_="check")
    op.create_check_constraint("messages_status_check", "messages", "status IN ('pending', 'streaming', 'completed', 'failed')")
