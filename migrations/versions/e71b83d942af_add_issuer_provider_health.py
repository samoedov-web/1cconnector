"""add issuer provider health

Revision ID: e71b83d942af
Revises: c24f1a8e7d31
"""

from alembic import op
import sqlalchemy as sa


revision = "e71b83d942af"
down_revision = "c24f1a8e7d31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("issuer_risk_checks") as batch:
        batch.add_column(
            sa.Column(
                "provider_health",
                sa.String(length=32),
                nullable=False,
                server_default="AVAILABLE",
            )
        )

    # Default нужен только для безопасного upgrade существующих строк.
    with op.batch_alter_table("issuer_risk_checks") as batch:
        batch.alter_column(
            "provider_health",
            existing_type=sa.String(length=32),
            existing_nullable=False,
            server_default=None,
        )


def downgrade() -> None:
    with op.batch_alter_table("issuer_risk_checks") as batch:
        batch.drop_column("provider_health")
