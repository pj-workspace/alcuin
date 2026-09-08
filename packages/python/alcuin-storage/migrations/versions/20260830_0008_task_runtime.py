"""Add the Workspace-scoped durable Task runtime schema."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260830_0008"
down_revision = "20260829_0007"
branch_labels = None
depends_on = None


TASK_STATUSES = (
    "draft",
    "planning",
    "ready",
    "running",
    "pause_requested",
    "paused",
    "waiting_for_approval",
    "waiting_for_user",
    "cancel_requested",
    "completed",
    "failed",
    "cancelled",
)
STEP_STATUSES = (
    "pending",
    "running",
    "waiting_for_approval",
    "waiting_for_user",
    "completed",
    "failed",
    "skipped",
    "cancelled",
)
ATTEMPT_STATUSES = (
    "queued",
    "running",
    "waiting_for_approval",
    "waiting_for_user",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
)


def _in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(repr(value) for value in values) + ")"


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("current_plan_id", sa.Text()),
        sa.Column("current_step_id", sa.Text()),
        # Internal compare-and-swap token. It is an integrity mechanism, not a
        # user-facing product-version resource.
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_event_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "next_checkpoint_sequence",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("result_json", sa.Text()),
        sa.Column("control_reason", sa.Text()),
        sa.Column("error_json", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.Text()),
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
        sa.CheckConstraint(
            "char_length(btrim(title)) BETWEEN 1 AND 200",
            name="tasks_title_length_check",
        ),
        sa.CheckConstraint(
            "char_length(btrim(goal)) BETWEEN 1 AND 4000",
            name="tasks_goal_length_check",
        ),
        sa.CheckConstraint(
            f"status IN {_in(TASK_STATUSES)}",
            name="tasks_status_check",
        ),
        sa.CheckConstraint("revision >= 0", name="tasks_revision_check"),
        sa.CheckConstraint(
            "next_event_sequence >= 0 AND next_checkpoint_sequence >= 0",
            name="tasks_sequences_check",
        ),
        sa.CheckConstraint(
            "(status IN ('completed', 'failed', 'cancelled') AND completed_at IS NOT NULL) "
            "OR (status NOT IN ('completed', 'failed', 'cancelled') AND completed_at IS NULL)",
            name="tasks_terminal_timestamp_check",
        ),
        sa.UniqueConstraint("id", "workspace_id", name="tasks_id_workspace_key"),
    )
    op.create_index(
        "idx_tasks_workspace_updated",
        "tasks",
        ["workspace_id", "updated_at"],
    )
    op.create_index(
        "idx_tasks_workspace_thread_updated",
        "tasks",
        ["workspace_id", "thread_id", "updated_at"],
    )

    op.create_table(
        "task_plans",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("goal_snapshot", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("superseded_at", sa.Text()),
        sa.ForeignKeyConstraint(
            ["task_id", "workspace_id"],
            ["tasks.id", "tasks.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("generation > 0", name="task_plans_generation_check"),
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'completed')",
            name="task_plans_status_check",
        ),
        sa.CheckConstraint(
            "(status = 'superseded' AND superseded_at IS NOT NULL) "
            "OR (status <> 'superseded' AND superseded_at IS NULL)",
            name="task_plans_superseded_timestamp_check",
        ),
        sa.UniqueConstraint("id", "workspace_id", name="task_plans_id_workspace_key"),
        sa.UniqueConstraint(
            "id",
            "task_id",
            "workspace_id",
            name="task_plans_id_task_workspace_key",
        ),
        sa.UniqueConstraint(
            "task_id",
            "generation",
            name="task_plans_task_generation_key",
        ),
    )
    op.create_index(
        "task_plans_one_active_per_task_key",
        "task_plans",
        ["task_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "task_steps",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("plan_id", sa.Text(), nullable=False),
        sa.Column("step_key", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("kind", sa.Text(), nullable=False, server_default="agent"),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("output_json", sa.Text()),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("error_json", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text()),
        sa.Column("completed_at", sa.Text()),
        sa.ForeignKeyConstraint(
            ["plan_id", "task_id", "workspace_id"],
            ["task_plans.id", "task_plans.task_id", "task_plans.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("position >= 0", name="task_steps_position_check"),
        sa.CheckConstraint(
            "step_key ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$'",
            name="task_steps_key_check",
        ),
        sa.CheckConstraint(
            "char_length(btrim(title)) BETWEEN 1 AND 160",
            name="task_steps_title_length_check",
        ),
        sa.CheckConstraint(
            "kind IN ('agent')",
            name="task_steps_kind_check",
        ),
        sa.CheckConstraint(
            f"status IN {_in(STEP_STATUSES)}",
            name="task_steps_status_check",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="task_steps_attempt_count_check"),
        sa.CheckConstraint(
            "(status IN ('completed', 'failed', 'skipped', 'cancelled') "
            "AND completed_at IS NOT NULL) OR "
            "(status NOT IN ('completed', 'failed', 'skipped', 'cancelled') "
            "AND completed_at IS NULL)",
            name="task_steps_terminal_timestamp_check",
        ),
        sa.UniqueConstraint("id", "workspace_id", name="task_steps_id_workspace_key"),
        sa.UniqueConstraint(
            "id",
            "task_id",
            "workspace_id",
            name="task_steps_id_task_workspace_key",
        ),
        sa.UniqueConstraint(
            "plan_id",
            "position",
            name="task_steps_plan_position_key",
        ),
        sa.UniqueConstraint(
            "plan_id",
            "step_key",
            name="task_steps_plan_key_key",
        ),
    )
    op.create_index(
        "idx_task_steps_workspace_task_position",
        "task_steps",
        ["workspace_id", "task_id", "position"],
    )

    op.create_foreign_key(
        "tasks_current_plan_workspace_fkey",
        "tasks",
        "task_plans",
        ["current_plan_id", "id", "workspace_id"],
        ["id", "task_id", "workspace_id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "tasks_current_step_workspace_fkey",
        "tasks",
        "task_steps",
        ["current_step_id", "id", "workspace_id"],
        ["id", "task_id", "workspace_id"],
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_table(
        "task_step_attempts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("step_id", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("input_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("output_json", sa.Text()),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("error_json", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.Text()),
        sa.ForeignKeyConstraint(
            ["step_id", "task_id", "workspace_id"],
            ["task_steps.id", "task_steps.task_id", "task_steps.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("attempt > 0", name="task_step_attempts_attempt_check"),
        sa.CheckConstraint(
            f"status IN {_in(ATTEMPT_STATUSES)}",
            name="task_step_attempts_status_check",
        ),
        sa.CheckConstraint(
            "(status IN ('completed', 'failed', 'cancelled', 'interrupted') "
            "AND completed_at IS NOT NULL) OR "
            "(status NOT IN ('completed', 'failed', 'cancelled', 'interrupted') "
            "AND completed_at IS NULL)",
            name="task_step_attempts_terminal_timestamp_check",
        ),
        sa.UniqueConstraint(
            "id",
            "workspace_id",
            name="task_step_attempts_id_workspace_key",
        ),
        sa.UniqueConstraint(
            "id",
            "task_id",
            "workspace_id",
            name="task_step_attempts_id_task_workspace_key",
        ),
        sa.UniqueConstraint(
            "step_id",
            "attempt",
            name="task_step_attempts_step_attempt_key",
        ),
    )

    op.create_table(
        "task_run_links",
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("step_id", sa.Text(), nullable=False),
        sa.Column("attempt_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id", "workspace_id"],
            ["tasks.id", "tasks.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["step_id", "task_id", "workspace_id"],
            ["task_steps.id", "task_steps.task_id", "task_steps.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id", "task_id", "workspace_id"],
            [
                "task_step_attempts.id",
                "task_step_attempts.task_id",
                "task_step_attempts.workspace_id",
            ],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            ["runs.id", "runs.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("task_id", "run_id", name="task_run_links_pkey"),
        sa.UniqueConstraint(
            "run_id",
            "workspace_id",
            name="task_run_links_run_workspace_key",
        ),
        sa.UniqueConstraint(
            "attempt_id",
            name="task_run_links_attempt_key",
        ),
    )

    op.create_table(
        "task_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("task_revision", sa.Integer(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id", "workspace_id"],
            ["tasks.id", "tasks.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("sequence > 0", name="task_events_sequence_check"),
        sa.CheckConstraint("task_revision >= 0", name="task_events_revision_check"),
        sa.CheckConstraint(
            "type ~ '^task[.][a-z0-9_.-]{1,80}$'",
            name="task_events_type_check",
        ),
        sa.UniqueConstraint(
            "task_id",
            "sequence",
            name="task_events_task_sequence_key",
        ),
    )
    op.create_index(
        "idx_task_events_workspace_task_sequence",
        "task_events",
        ["workspace_id", "task_id", "sequence"],
    )

    op.create_table(
        "task_commands",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("request_sha256", sa.Text(), nullable=False),
        sa.Column("result_revision", sa.Integer(), nullable=False),
        sa.Column("result_status", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id", "workspace_id"],
            ["tasks.id", "tasks.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "char_length(btrim(idempotency_key)) BETWEEN 1 AND 200",
            name="task_commands_idempotency_key_check",
        ),
        sa.CheckConstraint(
            "command ~ '^[a-z][a-z0-9_.-]{1,80}$'",
            name="task_commands_command_check",
        ),
        sa.CheckConstraint(
            "char_length(request_sha256) = 64",
            name="task_commands_request_sha256_check",
        ),
        sa.CheckConstraint(
            "result_revision >= 0",
            name="task_commands_result_revision_check",
        ),
        sa.CheckConstraint(
            f"result_status IN {_in(TASK_STATUSES)}",
            name="task_commands_result_status_check",
        ),
        sa.UniqueConstraint(
            "task_id",
            "idempotency_key",
            name="task_commands_task_idempotency_key",
        ),
    )

    op.create_table(
        "task_checkpoints",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("step_id", sa.Text()),
        sa.Column("attempt_id", sa.Text()),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("task_revision", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("state_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id", "workspace_id"],
            ["tasks.id", "tasks.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["step_id", "task_id", "workspace_id"],
            ["task_steps.id", "task_steps.task_id", "task_steps.workspace_id"],
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id", "task_id", "workspace_id"],
            [
                "task_step_attempts.id",
                "task_step_attempts.task_id",
                "task_step_attempts.workspace_id",
            ],
        ),
        sa.CheckConstraint("sequence > 0", name="task_checkpoints_sequence_check"),
        sa.CheckConstraint(
            "task_revision >= 0",
            name="task_checkpoints_revision_check",
        ),
        sa.UniqueConstraint(
            "task_id",
            "sequence",
            name="task_checkpoints_task_sequence_key",
        ),
    )
    op.create_index(
        "idx_task_checkpoints_workspace_task_sequence",
        "task_checkpoints",
        ["workspace_id", "task_id", "sequence"],
    )

    op.create_table(
        "task_interventions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("step_id", sa.Text()),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("applied_at", sa.Text()),
        sa.ForeignKeyConstraint(
            ["task_id", "workspace_id"],
            ["tasks.id", "tasks.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["step_id", "task_id", "workspace_id"],
            ["task_steps.id", "task_steps.task_id", "task_steps.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "kind IN ('queue', 'steer', 'interrupt')",
            name="task_interventions_kind_check",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'applied', 'rejected')",
            name="task_interventions_status_check",
        ),
        sa.CheckConstraint(
            "char_length(btrim(content)) BETWEEN 1 AND 40000",
            name="task_interventions_content_length_check",
        ),
        sa.CheckConstraint(
            "(status = 'applied' AND applied_at IS NOT NULL) "
            "OR (status <> 'applied' AND applied_at IS NULL)",
            name="task_interventions_applied_timestamp_check",
        ),
        sa.UniqueConstraint(
            "id",
            "workspace_id",
            name="task_interventions_id_workspace_key",
        ),
    )
    op.create_index(
        "idx_task_interventions_workspace_task_created",
        "task_interventions",
        ["workspace_id", "task_id", "created_at"],
    )

    op.create_table(
        "task_dispatch",
        sa.Column("task_id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("available_at", sa.Text(), nullable=False),
        sa.Column("lease_owner", sa.Text()),
        sa.Column("lease_expires_at", sa.Text()),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id", "workspace_id"],
            ["tasks.id", "tasks.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('ready', 'claimed', 'blocked', 'terminal')",
            name="task_dispatch_status_check",
        ),
        sa.CheckConstraint(
            "(status = 'claimed' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (status <> 'claimed' AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="task_dispatch_lease_check",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND revision >= 0",
            name="task_dispatch_counters_check",
        ),
        sa.UniqueConstraint(
            "task_id",
            "workspace_id",
            name="task_dispatch_id_workspace_key",
        ),
    )
    op.create_index(
        "idx_task_dispatch_ready",
        "task_dispatch",
        ["status", "available_at"],
        postgresql_where=sa.text("status = 'ready'"),
    )


def downgrade() -> None:
    op.drop_index("idx_task_dispatch_ready", table_name="task_dispatch")
    op.drop_table("task_dispatch")
    op.drop_index(
        "idx_task_interventions_workspace_task_created",
        table_name="task_interventions",
    )
    op.drop_table("task_interventions")
    op.drop_index(
        "idx_task_checkpoints_workspace_task_sequence",
        table_name="task_checkpoints",
    )
    op.drop_table("task_checkpoints")
    op.drop_index(
        "idx_task_events_workspace_task_sequence",
        table_name="task_events",
    )
    op.drop_table("task_commands")
    op.drop_table("task_events")
    op.drop_table("task_run_links")
    op.drop_table("task_step_attempts")
    op.drop_constraint(
        "tasks_current_step_workspace_fkey",
        "tasks",
        type_="foreignkey",
    )
    op.drop_constraint(
        "tasks_current_plan_workspace_fkey",
        "tasks",
        type_="foreignkey",
    )
    op.drop_index(
        "idx_task_steps_workspace_task_position",
        table_name="task_steps",
    )
    op.drop_table("task_steps")
    op.drop_index("task_plans_one_active_per_task_key", table_name="task_plans")
    op.drop_table("task_plans")
    op.drop_index("idx_tasks_workspace_thread_updated", table_name="tasks")
    op.drop_index("idx_tasks_workspace_updated", table_name="tasks")
    op.drop_table("tasks")
