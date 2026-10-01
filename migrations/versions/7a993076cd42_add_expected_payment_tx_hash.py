"""add expected payment tx hash

Revision ID: 7a993076cd42
Revises: 3e912313456f

"""
from alembic import op
import sqlalchemy as sa


revision = "7a993076cd42"
down_revision = "3e912313456f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("expected_payments") as batch:
        batch.add_column(
            sa.Column(
                "expected_tx_hash",
                sa.String(length=128),
                nullable=True,
            )
        )
        batch.create_index(
            "ix_expected_payments_expected_tx_hash",
            ["expected_tx_hash"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("expected_payments") as batch:
        batch.drop_index(
            "ix_expected_payments_expected_tx_hash"
        )
        batch.drop_column("expected_tx_hash")
