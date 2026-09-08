"""Persist per-Task model and reasoning controls across durable execution."""

from alembic import op
import sqlalchemy as sa


revision = "20260908_0009"
down_revision = "20260830_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("model_override", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("reasoning_effort", sa.Text(), nullable=True))
    op.create_check_constraint(
        "tasks_model_override_check",
        "tasks",
        "model_override IS NULL OR (char_length(model_override) BETWEEN 1 AND 200 "
        "AND model_override ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]*$')",
    )
    op.create_check_constraint(
        "tasks_reasoning_effort_check",
        "tasks",
        "reasoning_effort IS NULL OR reasoning_effort IN ('none', 'low', 'medium', 'high')",
    )


def downgrade() -> None:
    op.drop_constraint("tasks_reasoning_effort_check", "tasks", type_="check")
    op.drop_constraint("tasks_model_override_check", "tasks", type_="check")
    op.drop_column("tasks", "reasoning_effort")
    op.drop_column("tasks", "model_override")
