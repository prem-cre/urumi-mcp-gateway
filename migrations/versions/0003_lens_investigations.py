"""Add redacted step previews and durable Lens-inspired investigations.

Revision ID: 0003_lens_investigations
Revises: 0002_agent_observability
Create Date: 2026-10-15
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_lens_investigations"
down_revision = "0002_agent_observability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_traces", sa.Column("agent_name", sa.String(120), nullable=False, server_default="dashboard-assistant"))
    op.add_column("agent_traces", sa.Column("input_preview", sa.Text()))
    op.add_column("agent_traces", sa.Column("output_preview", sa.Text()))
    op.add_column("agent_traces", sa.Column("content_captured", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_agent_traces_agent_name", "agent_traces", ["agent_name"])
    op.add_column("agent_observations", sa.Column("input_preview", sa.Text()))
    op.add_column("agent_observations", sa.Column("output_preview", sa.Text()))

    op.create_table(
        "agent_investigations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("expected_behavior", sa.Text(), nullable=False),
        sa.Column("checks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("window_hours", sa.Integer(), nullable=False),
        sa.Column("sample_limit", sa.Integer(), nullable=False),
        sa.Column("max_cost_usd", sa.Numeric(18, 10), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_investigations_created_by", "agent_investigations", ["created_by"])

    op.create_table(
        "investigation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("investigation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_investigations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("selected_trace_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sampled_count", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(160)),
        sa.Column("provider", sa.String(120)),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("estimated_cost_usd", sa.Numeric(18, 10)),
        sa.Column("pricing_status", sa.String(24), nullable=False),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("error_category", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(64)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("investigation_id", "idempotency_key", name="uq_investigation_run_idempotency"),
    )
    op.create_index("ix_investigation_runs_investigation_id", "investigation_runs", ["investigation_id"])
    op.create_index("ix_investigation_runs_status", "investigation_runs", ["status"])
    op.create_index("ix_investigation_runs_lease_until", "investigation_runs", ["lease_until"])
    op.create_index("ix_investigation_runs_created_at", "investigation_runs", ["created_at"])
    op.create_index(
        "uq_investigation_active_run",
        "investigation_runs",
        ["investigation_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )

    op.create_table(
        "investigation_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("investigation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("feedback", sa.Text()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_investigation_findings_run_id", "investigation_findings", ["run_id"])


def downgrade() -> None:
    op.drop_table("investigation_findings")
    op.drop_index("uq_investigation_active_run", table_name="investigation_runs")
    op.drop_index("ix_investigation_runs_created_at", table_name="investigation_runs")
    op.drop_index("ix_investigation_runs_lease_until", table_name="investigation_runs")
    op.drop_index("ix_investigation_runs_status", table_name="investigation_runs")
    op.drop_index("ix_investigation_runs_investigation_id", table_name="investigation_runs")
    op.drop_table("investigation_runs")
    op.drop_index("ix_agent_investigations_created_by", table_name="agent_investigations")
    op.drop_table("agent_investigations")
    op.drop_column("agent_observations", "output_preview")
    op.drop_column("agent_observations", "input_preview")
    op.drop_index("ix_agent_traces_agent_name", table_name="agent_traces")
    op.drop_column("agent_traces", "content_captured")
    op.drop_column("agent_traces", "output_preview")
    op.drop_column("agent_traces", "input_preview")
    op.drop_column("agent_traces", "agent_name")
