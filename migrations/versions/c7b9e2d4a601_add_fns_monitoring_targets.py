"""add FNS monitoring targets

Revision ID: c7b9e2d4a601
Revises: a6d4e8f2c310

Mutable scheduler state only.
MarketQuoteEvidence remains immutable and separate.
"""

from alembic import op
import sqlalchemy as sa


revision = "c7b9e2d4a601"
down_revision = "a6d4e8f2c310"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fns_monitoring_targets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("base_asset", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "refresh_interval_seconds",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "last_checked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "next_check_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "lease_owner",
            sa.String(128),
            nullable=True,
        ),
        sa.Column(
            "lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "base_asset",
            "source_id",
            name="uq_fns_monitoring_target_asset_source",
        ),
    )


def downgrade() -> None:
    op.drop_table("fns_monitoring_targets")
