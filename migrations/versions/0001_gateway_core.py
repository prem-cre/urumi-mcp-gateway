"""Create the gateway identity, registry, catalog and audit schema.

Revision ID: 0001_gateway_core
Revises:
Create Date: 2026-10-15
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_gateway_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    op.create_table(
        "web_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("csrf_digest", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_web_sessions_user_id", "web_sessions", ["user_id"])
    op.create_index("ix_web_sessions_token_digest", "web_sessions", ["token_digest"])
    op.create_table(
        "invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_invitations_email", "invitations", ["email"])
    op.create_index("ix_invitations_token_digest", "invitations", ["token_digest"])
    op.create_table(
        "upstream_servers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("alias", sa.String(48), nullable=False, unique=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(240), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("auth_header", sa.String(100)),
        sa.Column("auth_prefix", sa.String(80), nullable=False),
        sa.Column("credential_ciphertext", sa.Text()),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("member_tools", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("last_check_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(240)),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_upstream_servers_alias", "upstream_servers", ["alias"])
    op.create_table(
        "discovered_tools",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "server_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("upstream_servers.id"),
            nullable=False,
        ),
        sa.Column("original_name", sa.String(256), nullable=False),
        sa.Column("public_name", sa.String(256), nullable=False, unique=True),
        sa.Column("description", sa.String(1000), nullable=False),
        sa.Column("input_schema", postgresql.JSONB(), nullable=False),
        sa.Column("output_schema", postgresql.JSONB()),
        sa.Column("annotations", postgresql.JSONB(), nullable=False),
        sa.Column("schema_hash", sa.String(64), nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("server_id", "original_name"),
    )
    op.create_index("ix_discovered_tools_server_id", "discovered_tools", ["server_id"])
    op.create_index("ix_discovered_tools_public_name", "discovered_tools", ["public_name"])
    op.create_table(
        "tool_audits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("key_id", sa.String(32)),
        sa.Column("server_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("upstream_servers.id")),
        sa.Column("user_email", sa.String(320)),
        sa.Column("server_alias", sa.String(48)),
        sa.Column("tool_name", sa.String(256)),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("error_category", sa.String(64)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_tool_audits_correlation_id", "tool_audits", ["correlation_id"])
    op.create_index("ix_tool_audits_user_id", "tool_audits", ["user_id"])
    op.create_index("ix_tool_audits_key_id", "tool_audits", ["key_id"])
    op.create_index("ix_tool_audits_outcome", "tool_audits", ["outcome"])
    op.create_index("ix_tool_audits_started_at", "tool_audits", ["started_at"])


def downgrade() -> None:
    op.drop_table("tool_audits")
    op.drop_table("discovered_tools")
    op.drop_table("upstream_servers")
    op.drop_table("invitations")
    op.drop_table("web_sessions")
    op.drop_table("api_keys")
    op.drop_table("users")
