"""add operation child links

Revision ID: a17d9c5f0b42
Revises: f42a6c190d73
"""

from alembic import op
import sqlalchemy as sa


revision = "a17d9c5f0b42"
down_revision = "f42a6c190d73"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("transactions") as batch:
        batch.add_column(
            sa.Column("operation_id", sa.Integer(), nullable=True)
        )
        batch.create_foreign_key(
            "fk_transactions_operation_id_operations",
            "operations",
            ["operation_id"],
            ["id"],
        )

    with op.batch_alter_table("matches") as batch:
        batch.add_column(
            sa.Column("operation_id", sa.Integer(), nullable=True)
        )
        batch.create_foreign_key(
            "fk_matches_operation_id_operations",
            "operations",
            ["operation_id"],
            ["id"],
        )

    with op.batch_alter_table("expected_payments") as batch:
        batch.add_column(
            sa.Column("operation_id", sa.Integer(), nullable=True)
        )
        batch.create_foreign_key(
            "fk_expected_payments_operation_id_operations",
            "operations",
            ["operation_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("expected_payments") as batch:
        batch.drop_constraint(
            "fk_expected_payments_operation_id_operations",
            type_="foreignkey",
        )
        batch.drop_column("operation_id")

    with op.batch_alter_table("matches") as batch:
        batch.drop_constraint(
            "fk_matches_operation_id_operations",
            type_="foreignkey",
        )
        batch.drop_column("operation_id")

    with op.batch_alter_table("transactions") as batch:
        batch.drop_constraint(
            "fk_transactions_operation_id_operations",
            type_="foreignkey",
        )
        batch.drop_column("operation_id")
