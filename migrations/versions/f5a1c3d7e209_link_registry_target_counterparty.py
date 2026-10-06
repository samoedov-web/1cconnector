"""link registry monitoring target to canonical counterparty

Registry subject_reference is an external registry identifier and cannot be
reliably inferred from Counterparty.name or onec_ref.  Impact analysis uses an
explicit nullable foreign key instead.

Revision ID: f5a1c3d7e209
Revises: e4c9b7a2d105
"""

from alembic import op
import sqlalchemy as sa


revision = "f5a1c3d7e209"
down_revision = "e4c9b7a2d105"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("registry_monitoring_targets") as batch:
        batch.add_column(
            sa.Column("counterparty_id", sa.Integer(), nullable=True)
        )
        batch.create_foreign_key(
            "fk_registry_monitoring_targets_counterparty_id",
            "counterparties",
            ["counterparty_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("registry_monitoring_targets") as batch:
        batch.drop_constraint(
            "fk_registry_monitoring_targets_counterparty_id",
            type_="foreignkey",
        )
        batch.drop_column("counterparty_id")
