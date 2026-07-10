"""onec doc_type reconciliation_info

Revision ID: 1793d82bbca1
Revises: 0462b203d108
Create Date: 2026-07-10 11:59:23.991440

"""
from alembic import op
import sqlalchemy as sa


revision = '1793d82bbca1'
down_revision = '0462b203d108'
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
