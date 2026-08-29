"""Add immutable conversation messages and traceable context assemblies."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260829_0002"
down_revision = "20260828_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "threads",
        sa.Column(
            "next_message_sequence",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column("threads", sa.Column("updated_at", sa.Text(), nullable=True))
    op.execute("UPDATE threads SET updated_at = created_at WHERE updated_at IS NULL")
    op.alter_column("threads", "updated_at", nullable=False)
    op.create_index(
        "idx_threads_workspace_updated",
        "threads",
        ["workspace_id", "updated_at"],
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Text(),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "thread_id",
            sa.Text(),
            sa.ForeignKey("threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.Text(),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "agent_version_id",
            sa.Text(),
            sa.ForeignKey("agent_versions.id"),
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("parts_json", sa.Text(), nullable=False),
        sa.Column(
            "estimated_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.Text()),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')",
            name="messages_role_check",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'streaming', 'completed', 'failed')",
            name="messages_status_check",
        ),
        sa.CheckConstraint("sequence > 0", name="messages_sequence_check"),
        sa.CheckConstraint(
            "estimated_tokens >= 0",
            name="messages_estimated_tokens_check",
        ),
        sa.UniqueConstraint(
            "thread_id",
            "sequence",
            name="messages_thread_id_sequence_key",
        ),
    )
    op.create_index(
        "messages_run_id_role_key",
        "messages",
        ["run_id", "role"],
        unique=True,
        postgresql_where=sa.text("run_id IS NOT NULL"),
    )
    op.create_index(
        "idx_messages_workspace_thread_sequence",
        "messages",
        ["workspace_id", "thread_id", "sequence"],
    )

    op.create_table(
        "thread_compactions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Text(),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "thread_id",
            sa.Text(),
            sa.ForeignKey("threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("parent_id", sa.Text(), sa.ForeignKey("thread_compactions.id")),
        sa.Column("through_sequence", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("source_digest", sa.Text(), nullable=False),
        sa.Column("source_message_ids_json", sa.Text(), nullable=False),
        sa.Column("estimated_source_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_summary_tokens", sa.Integer(), nullable=False),
        sa.Column("strategy", sa.Text(), nullable=False),
        sa.Column("created_by_run_id", sa.Text(), sa.ForeignKey("runs.id")),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "through_sequence > 0",
            name="thread_compactions_sequence_check",
        ),
        sa.CheckConstraint(
            "estimated_source_tokens >= 0 AND estimated_summary_tokens >= 0",
            name="thread_compactions_token_counts_check",
        ),
        sa.UniqueConstraint(
            "thread_id",
            "through_sequence",
            name="thread_compactions_thread_sequence_key",
        ),
    )
    op.create_index(
        "idx_thread_compactions_workspace_thread_sequence",
        "thread_compactions",
        ["workspace_id", "thread_id", "through_sequence"],
    )

    op.create_table(
        "run_context_assemblies",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Text(),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "thread_id",
            sa.Text(),
            sa.ForeignKey("threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.Text(),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "agent_version_id",
            sa.Text(),
            sa.ForeignKey("agent_versions.id"),
            nullable=False,
        ),
        sa.Column("entries_json", sa.Text(), nullable=False),
        sa.Column("normalized_input_json", sa.Text(), nullable=False),
        sa.Column("estimated_input_tokens", sa.Integer(), nullable=False),
        sa.Column("effective_budget_tokens", sa.Integer(), nullable=False),
        sa.Column("compaction_trigger_tokens", sa.Integer(), nullable=False),
        sa.Column("message_sequence_through", sa.Integer(), nullable=False),
        sa.Column(
            "active_compaction_id",
            sa.Text(),
            sa.ForeignKey("thread_compactions.id"),
        ),
        sa.Column("estimator_revision", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "estimated_input_tokens >= 0 AND effective_budget_tokens > 0 "
            "AND compaction_trigger_tokens > 0",
            name="run_context_assemblies_token_counts_check",
        ),
        sa.CheckConstraint(
            "message_sequence_through > 0",
            name="run_context_assemblies_sequence_check",
        ),
    )
    op.create_index(
        "idx_run_context_assemblies_workspace_run",
        "run_context_assemblies",
        ["workspace_id", "run_id"],
    )

    # Preserve the pre-alpha history. User prompts come from Runs; completed assistant text is the
    # ordered visible message stream. Raw events remain authoritative even when no assistant text
    # was emitted (for example, a failed Run).
    op.execute(
        """
        WITH assistant_text AS (
            SELECT
                r.id AS run_id,
                string_agg(e.payload_json::jsonb ->> 'delta', '' ORDER BY e.sequence) AS content
            FROM runs r
            JOIN events e ON e.run_id = r.id AND e.type = 'message.delta'
            WHERE r.status = 'completed'
            GROUP BY r.id
        ), candidates AS (
            SELECT
                r.workspace_id,
                r.thread_id,
                r.id AS run_id,
                r.agent_version_id,
                'user'::text AS role,
                r.input AS content,
                r.created_at AS sort_at,
                0 AS role_order
            FROM runs r
            UNION ALL
            SELECT
                r.workspace_id,
                r.thread_id,
                r.id AS run_id,
                r.agent_version_id,
                'assistant'::text AS role,
                a.content,
                COALESCE(r.completed_at, r.created_at) AS sort_at,
                1 AS role_order
            FROM runs r
            JOIN assistant_text a ON a.run_id = r.id
            WHERE length(a.content) > 0
        ), ordered AS (
            SELECT
                c.*,
                row_number() OVER (
                    PARTITION BY c.thread_id
                    ORDER BY c.sort_at, c.run_id, c.role_order
                ) AS message_sequence
            FROM candidates c
        )
        INSERT INTO messages(
            id, workspace_id, thread_id, run_id, agent_version_id, sequence, role,
            status, parts_json, estimated_tokens, created_at, completed_at
        )
        SELECT
            'msg_' || substr(md5(run_id || ':' || role), 1, 20),
            workspace_id,
            thread_id,
            run_id,
            agent_version_id,
            message_sequence,
            role,
            'completed',
            jsonb_build_array(jsonb_build_object('type', 'text', 'text', content))::text,
            CASE WHEN length(content) = 0 THEN 0
                 ELSE greatest(1, ceil(octet_length(content)::numeric / 3)::integer)
            END,
            sort_at,
            sort_at
        FROM ordered
        """
    )
    op.execute(
        """
        UPDATE threads t
        SET
            next_message_sequence = state.max_sequence,
            updated_at = state.latest_at
        FROM (
            SELECT
                thread_id,
                max(sequence) AS max_sequence,
                max(created_at) AS latest_at
            FROM messages
            GROUP BY thread_id
        ) state
        WHERE state.thread_id = t.id
        """
    )

    op.create_index(
        "runs_one_active_per_thread_key",
        "runs",
        ["thread_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('queued', 'running', 'waiting_for_approval')"
        ),
    )


def downgrade() -> None:
    op.drop_index("runs_one_active_per_thread_key", table_name="runs")
    op.drop_index(
        "idx_run_context_assemblies_workspace_run",
        table_name="run_context_assemblies",
    )
    op.drop_table("run_context_assemblies")
    op.drop_index(
        "idx_thread_compactions_workspace_thread_sequence",
        table_name="thread_compactions",
    )
    op.drop_table("thread_compactions")
    op.drop_index("idx_messages_workspace_thread_sequence", table_name="messages")
    op.drop_index("messages_run_id_role_key", table_name="messages")
    op.drop_table("messages")
    op.drop_index("idx_threads_workspace_updated", table_name="threads")
    op.drop_column("threads", "updated_at")
    op.drop_column("threads", "next_message_sequence")
