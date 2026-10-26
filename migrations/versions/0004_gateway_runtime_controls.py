"""Add shared gateway rate-limit buckets.

Revision ID: 0004_gateway_runtime_controls
Revises: 0003_lens_investigations
Create Date: 2026-10-19
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_gateway_runtime_controls"
down_revision = "0003_lens_investigations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gateway_rate_limit_buckets",
        sa.Column("key_hash", sa.String(64), primary_key=True),
        sa.Column("window_start", sa.Integer(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("gateway_rate_limit_buckets")
