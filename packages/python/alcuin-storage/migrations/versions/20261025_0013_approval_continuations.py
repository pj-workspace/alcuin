"""Keep model-loop approval transcripts outside public approval records."""
from alembic import op
import sqlalchemy as sa

revision = "20261025_0013"
down_revision = "20260922_0012"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "approval_continuations",
        sa.Column("approval_id", sa.Text(), sa.ForeignKey("approvals.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_json", sa.Text()),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM approval_continuations WHERE checkpoint_json IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("Resolve approval continuations before downgrading")
    op.drop_table("approval_continuations")
