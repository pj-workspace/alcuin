"""Add Workspace-scoped Skills, Rules, and next-turn configuration."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260829_0004"
down_revision = "20260829_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Composite keys let every new relationship encode Workspace ownership in PostgreSQL,
    # rather than relying only on globally unique text identifiers.
    op.create_unique_constraint(
        "agent_versions_id_workspace_key",
        "agent_versions",
        ["id", "workspace_id"],
    )
    op.create_unique_constraint(
        "threads_id_workspace_key",
        "threads",
        ["id", "workspace_id"],
    )

    op.create_table(
        "skills",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("current_version_id", sa.Text()),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.CheckConstraint(
            "source_kind IN ('native', 'agent_plugin', 'cursor_plugin')",
            name="skills_source_kind_check",
        ),
        sa.UniqueConstraint("id", "workspace_id", name="skills_id_workspace_key"),
        sa.UniqueConstraint(
            "workspace_id",
            "slug",
            name="skills_workspace_id_slug_key",
        ),
    )
    op.create_table(
        "skill_versions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("skill_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition_json", sa.Text(), nullable=False),
        sa.Column("definition_sha256", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["skill_id", "workspace_id"],
            ["skills.id", "skills.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("version > 0", name="skill_versions_version_check"),
        sa.CheckConstraint(
            "char_length(definition_sha256) = 64",
            name="skill_versions_definition_sha256_check",
        ),
        sa.UniqueConstraint(
            "id",
            "workspace_id",
            name="skill_versions_id_workspace_key",
        ),
        sa.UniqueConstraint(
            "skill_id",
            "version",
            name="skill_versions_skill_id_version_key",
        ),
    )
    op.create_foreign_key(
        "skills_current_version_workspace_fkey",
        "skills",
        "skill_versions",
        ["current_version_id", "workspace_id"],
        ["id", "workspace_id"],
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_table(
        "rules",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("thread_id", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("current_version_id", sa.Text()),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(
            ["thread_id", "workspace_id"],
            ["threads.id", "threads.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "scope IN ('workspace', 'thread', 'library')",
            name="rules_scope_check",
        ),
        sa.CheckConstraint(
            "(scope = 'thread' AND thread_id IS NOT NULL) OR "
            "(scope <> 'thread' AND thread_id IS NULL)",
            name="rules_scope_thread_check",
        ),
        sa.CheckConstraint(
            "source_kind IN ('native', 'agent_plugin', 'cursor_plugin')",
            name="rules_source_kind_check",
        ),
        sa.UniqueConstraint("id", "workspace_id", name="rules_id_workspace_key"),
        sa.UniqueConstraint(
            "workspace_id",
            "slug",
            name="rules_workspace_id_slug_key",
        ),
    )
    op.create_table(
        "rule_versions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition_json", sa.Text(), nullable=False),
        sa.Column("definition_sha256", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["rule_id", "workspace_id"],
            ["rules.id", "rules.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("version > 0", name="rule_versions_version_check"),
        sa.CheckConstraint(
            "char_length(definition_sha256) = 64",
            name="rule_versions_definition_sha256_check",
        ),
        sa.UniqueConstraint(
            "id",
            "workspace_id",
            name="rule_versions_id_workspace_key",
        ),
        sa.UniqueConstraint(
            "rule_id",
            "version",
            name="rule_versions_rule_id_version_key",
        ),
    )
    op.create_foreign_key(
        "rules_current_version_workspace_fkey",
        "rules",
        "rule_versions",
        ["current_version_id", "workspace_id"],
        ["id", "workspace_id"],
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_table(
        "agent_version_skill_bindings",
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("agent_version_id", sa.Text(), nullable=False),
        sa.Column("skill_version_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_version_id", "workspace_id"],
            ["agent_versions.id", "agent_versions.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["skill_version_id", "workspace_id"],
            ["skill_versions.id", "skill_versions.workspace_id"],
        ),
        sa.CheckConstraint(
            "mode IN ('auto', 'always', 'manual')",
            name="agent_version_skill_bindings_mode_check",
        ),
        sa.CheckConstraint("position >= 0", name="agent_skill_bindings_position_check"),
        sa.PrimaryKeyConstraint("agent_version_id", "skill_version_id"),
        sa.UniqueConstraint(
            "agent_version_id",
            "position",
            name="agent_version_skill_bindings_position_key",
        ),
    )
    op.create_table(
        "agent_version_rule_bindings",
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("agent_version_id", sa.Text(), nullable=False),
        sa.Column("rule_version_id", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_version_id", "workspace_id"],
            ["agent_versions.id", "agent_versions.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rule_version_id", "workspace_id"],
            ["rule_versions.id", "rule_versions.workspace_id"],
        ),
        sa.CheckConstraint("position >= 0", name="agent_rule_bindings_position_check"),
        sa.PrimaryKeyConstraint("agent_version_id", "rule_version_id"),
        sa.UniqueConstraint(
            "agent_version_id",
            "position",
            name="agent_version_rule_bindings_position_key",
        ),
    )

    op.create_table(
        "thread_configurations",
        sa.Column("thread_id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["thread_id", "workspace_id"],
            ["threads.id", "threads.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name="thread_configurations_revision_check",
        ),
        sa.UniqueConstraint(
            "thread_id",
            "workspace_id",
            name="thread_configurations_id_workspace_key",
        ),
    )
    op.create_table(
        "thread_configuration_skills",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("skill_version_id", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["thread_id", "workspace_id"],
            ["thread_configurations.thread_id", "thread_configurations.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["skill_version_id", "workspace_id"],
            ["skill_versions.id", "skill_versions.workspace_id"],
        ),
        sa.CheckConstraint("position >= 0", name="thread_config_skills_position_check"),
        sa.PrimaryKeyConstraint("thread_id", "skill_version_id"),
        sa.UniqueConstraint(
            "thread_id",
            "position",
            name="thread_configuration_skills_position_key",
        ),
    )
    op.create_table(
        "thread_configuration_rules",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("rule_version_id", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["thread_id", "workspace_id"],
            ["thread_configurations.thread_id", "thread_configurations.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rule_version_id", "workspace_id"],
            ["rule_versions.id", "rule_versions.workspace_id"],
        ),
        sa.CheckConstraint("position >= 0", name="thread_config_rules_position_check"),
        sa.PrimaryKeyConstraint("thread_id", "rule_version_id"),
        sa.UniqueConstraint(
            "thread_id",
            "position",
            name="thread_configuration_rules_position_key",
        ),
    )
    op.create_table(
        "workspace_preferences",
        sa.Column("workspace_id", sa.Text(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("revision >= 0", name="workspace_preferences_revision_check"),
        sa.CheckConstraint(
            "char_length(content_sha256) = 64",
            name="workspace_preferences_content_sha256_check",
        ),
    )

    op.execute(
        """
        INSERT INTO thread_configurations(
            thread_id, workspace_id, revision, created_at, updated_at
        )
        SELECT id, workspace_id, 0, created_at, updated_at
        FROM threads
        ON CONFLICT (thread_id) DO NOTHING
        """
    )

    op.create_index("idx_skills_workspace_updated", "skills", ["workspace_id", "updated_at"])
    op.create_index(
        "idx_skill_versions_workspace_skill_version",
        "skill_versions",
        ["workspace_id", "skill_id", "version"],
    )
    op.create_index("idx_rules_workspace_updated", "rules", ["workspace_id", "updated_at"])
    op.create_index(
        "idx_rule_versions_workspace_rule_version",
        "rule_versions",
        ["workspace_id", "rule_id", "version"],
    )
    op.create_index(
        "idx_thread_configurations_workspace",
        "thread_configurations",
        ["workspace_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_thread_configurations_workspace", table_name="thread_configurations")
    op.drop_index(
        "idx_rule_versions_workspace_rule_version",
        table_name="rule_versions",
    )
    op.drop_index("idx_rules_workspace_updated", table_name="rules")
    op.drop_index(
        "idx_skill_versions_workspace_skill_version",
        table_name="skill_versions",
    )
    op.drop_index("idx_skills_workspace_updated", table_name="skills")
    for table in (
        "workspace_preferences",
        "thread_configuration_rules",
        "thread_configuration_skills",
        "thread_configurations",
        "agent_version_rule_bindings",
        "agent_version_skill_bindings",
    ):
        op.drop_table(table)
    op.drop_constraint(
        "rules_current_version_workspace_fkey",
        "rules",
        type_="foreignkey",
    )
    op.drop_table("rule_versions")
    op.drop_table("rules")
    op.drop_constraint(
        "skills_current_version_workspace_fkey",
        "skills",
        type_="foreignkey",
    )
    op.drop_table("skill_versions")
    op.drop_table("skills")
    op.drop_constraint("threads_id_workspace_key", "threads", type_="unique")
    op.drop_constraint(
        "agent_versions_id_workspace_key",
        "agent_versions",
        type_="unique",
    )
