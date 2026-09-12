"""Persist bounded asynchronous Thread naming claims."""

from alembic import op
import sqlalchemy as sa

revision = "20260912_0011"
down_revision = "20260908_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "threads",
        sa.Column("title_status", sa.Text(), nullable=False, server_default="pending"),
    )
    op.add_column("threads", sa.Column("title_claim", sa.Text(), nullable=True))
    op.add_column("threads", sa.Column("title_claim_until", sa.Text(), nullable=True))
    op.execute(
        "UPDATE threads SET title_status = 'ready' WHERE trim(title) NOT IN ('', 'Working session', 'New agent thread')"
    )
    op.create_check_constraint(
        "threads_title_status_check",
        "threads",
        "title_status IN ('pending', 'generating', 'ready')",
    )


def downgrade() -> None:
    op.drop_constraint("threads_title_status_check", "threads", type_="check")
    op.drop_column("threads", "title_claim_until")
    op.drop_column("threads", "title_claim")
    op.drop_column("threads", "title_status")
