"""Add exact FNS quotation selectors.

Revision ID: d8e5f3a1b702
Revises: c7b9e2d4a601
"""

from alembic import op
import sqlalchemy as sa

revision = "d8e5f3a1b702"
down_revision = "c7b9e2d4a601"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fns_monitoring_targets",
        sa.Column(
            "organizer", sa.String(256), nullable=True
        ),
    )
    op.add_column(
        "fns_monitoring_targets",
        sa.Column(
            "trading_pair", sa.String(128), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column(
        "fns_monitoring_targets", "trading_pair"
    )
    op.drop_column(
        "fns_monitoring_targets", "organizer"
    )
