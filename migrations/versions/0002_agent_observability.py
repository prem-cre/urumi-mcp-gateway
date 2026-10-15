"""Add dashboard agent traces and model/tool observations.

Revision ID: 0002_agent_observability
Revises: 0001_gateway_core
Create Date: 2026-10-15
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_agent_observability"
down_revision = "0001_gateway_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_traces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("user_email", sa.String(320), nullable=False),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("error_category", sa.String(64)),
        sa.Column("route_tier", sa.String(24)),
        sa.Column("route_reason", sa.String(240)),
        sa.Column("selected_model", sa.String(160)),
        sa.Column("provider", sa.String(120)),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("total_tokens", sa.Integer()),
        sa.Column("estimated_cost_usd", sa.Numeric(18, 10)),
        sa.Column("pricing_status", sa.String(24), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer()),
    )
    op.create_index("ix_agent_traces_user_id", "agent_traces", ["user_id"])
    op.create_index("ix_agent_traces_outcome", "agent_traces", ["outcome"])
    op.create_index("ix_agent_traces_started_at", "agent_traces", ["started_at"])

    op.create_table(
        "agent_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "trace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_traces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("server_alias", sa.String(48)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("model", sa.String(160)),
        sa.Column("provider", sa.String(120)),
        sa.Column("route_tier", sa.String(24)),
        sa.Column("route_reason", sa.String(240)),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("total_tokens", sa.Integer()),
        sa.Column("input_cost_usd", sa.Numeric(18, 10)),
        sa.Column("output_cost_usd", sa.Numeric(18, 10)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("error_category", sa.String(64)),
    )
    op.create_index("ix_agent_observations_trace_id", "agent_observations", ["trace_id"])
    op.create_index("ix_agent_observations_kind", "agent_observations", ["kind"])
    op.create_index("ix_agent_observations_status", "agent_observations", ["status"])


def downgrade() -> None:
    op.drop_table("agent_observations")
    op.drop_table("agent_traces")
