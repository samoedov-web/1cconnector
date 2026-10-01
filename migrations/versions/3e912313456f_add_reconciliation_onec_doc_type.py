"""add reconciliation onec doc type

Revision ID: 3e912313456f
Revises: bed363537d3d

"""
from alembic import op
import sqlalchemy as sa


revision = "3e912313456f"
down_revision = "bed363537d3d"
branch_labels = None
depends_on = None


NEW_TYPE = sa.Enum(
    'RECEIPT', 'DISPOSAL', 'REVALUATION', 'FEE', 'RECONCILIATION',
    name='onecdoctype', native_enum=False,
)


def upgrade() -> None:
    # Новое значение enum reconciliation_info длиннее прежнего максимума —
    # расширяем VARCHAR; batch-режим для совместимости с SQLite.
    with op.batch_alter_table('onec_documents') as batch:
        batch.alter_column(
            'doc_type',
            existing_type=sa.VARCHAR(length=11),
            type_=NEW_TYPE,
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table('onec_documents') as batch:
        batch.alter_column(
            'doc_type',
            existing_type=NEW_TYPE,
            type_=sa.VARCHAR(length=11),
            existing_nullable=False,
        )
