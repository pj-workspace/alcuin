"""Add Workspace-scoped staged attachment resources and atomic message bindings."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260829_0006"
down_revision = "20260829_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "messages_id_workspace_key",
        "messages",
        ["id", "workspace_id"],
    )
    op.create_table(
        "attachments",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("upload_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "kind IN ('image', 'document')",
            name="attachments_kind_check",
        ),
        sa.CheckConstraint(
            "status IN ('ready', 'failed')",
            name="attachments_status_check",
        ),
        sa.CheckConstraint("size_bytes > 0", name="attachments_size_check"),
        sa.CheckConstraint(
            "char_length(sha256) = 64",
            name="attachments_sha256_check",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name="attachments_workspace_id_key",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "upload_id",
            name="attachments_workspace_upload_id_key",
        ),
    )
    op.create_index(
        "idx_attachments_workspace_expiry",
        "attachments",
        ["workspace_id", "expires_at"],
    )
    op.create_table(
        "attachment_blobs",
        sa.Column("attachment_id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("extracted_text", sa.Text()),
        sa.ForeignKeyConstraint(
            ["attachment_id", "workspace_id"],
            ["attachments.id", "attachments.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "attachment_id",
            "workspace_id",
            name="attachment_blobs_attachment_workspace_key",
        ),
    )
    op.create_table(
        "message_attachments",
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("attachment_id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id", "workspace_id"],
            ["messages.id", "messages.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["attachment_id", "workspace_id"],
            ["attachments.id", "attachments.workspace_id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("position >= 0", name="message_attachments_position_check"),
        sa.PrimaryKeyConstraint(
            "message_id",
            "position",
            name="message_attachments_pkey",
        ),
        sa.UniqueConstraint(
            "attachment_id",
            name="message_attachments_attachment_id_key",
        ),
    )
    op.create_index(
        "idx_message_attachments_workspace_message",
        "message_attachments",
        ["workspace_id", "message_id", "position"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_message_attachments_workspace_message",
        table_name="message_attachments",
    )
    op.drop_table("message_attachments")
    op.drop_table("attachment_blobs")
    op.drop_index("idx_attachments_workspace_expiry", table_name="attachments")
    op.drop_table("attachments")
    op.drop_constraint("messages_id_workspace_key", "messages", type_="unique")
