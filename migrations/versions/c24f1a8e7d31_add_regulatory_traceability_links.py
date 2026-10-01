"""add regulatory traceability links

Revision ID: c24f1a8e7d31
Revises: 7a993076cd42
"""

from alembic import op
import sqlalchemy as sa


revision = "c24f1a8e7d31"
down_revision = "7a993076cd42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("rate_snapshots") as batch:
        batch.add_column(
            sa.Column("regulatory_version_id", sa.Integer(), nullable=True)
        )
        batch.create_foreign_key(
            "fk_rate_snapshots_regulatory_version",
            "regulatory_versions",
            ["regulatory_version_id"],
            ["id"],
        )

    with op.batch_alter_table("evidence_links") as batch:
        batch.add_column(
            sa.Column("regulatory_version_id", sa.Integer(), nullable=True)
        )
        batch.create_foreign_key(
            "fk_evidence_links_regulatory_version",
            "regulatory_versions",
            ["regulatory_version_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("evidence_links") as batch:
        batch.drop_constraint(
            "fk_evidence_links_regulatory_version",
            type_="foreignkey",
        )
        batch.drop_column("regulatory_version_id")

    with op.batch_alter_table("rate_snapshots") as batch:
        batch.drop_constraint(
            "fk_rate_snapshots_regulatory_version",
            type_="foreignkey",
        )
        batch.drop_column("regulatory_version_id")
