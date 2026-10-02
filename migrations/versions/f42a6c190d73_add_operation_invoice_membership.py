"""add operation invoice membership

Revision ID: f42a6c190d73
Revises: e71b83d942af
"""

from alembic import op
import sqlalchemy as sa


revision = "f42a6c190d73"
down_revision = "e71b83d942af"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operation_invoices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["operations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["invoices.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operation_id",
            "invoice_id",
            name="uq_operation_invoice_membership",
        ),
    )


def downgrade() -> None:
    op.drop_table("operation_invoices")
