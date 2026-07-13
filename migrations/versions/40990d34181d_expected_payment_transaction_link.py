"""expected payment transaction link

Revision ID: 40990d34181d
Revises: 2c241b20686a
Create Date: 2026-07-13 09:13:15.477222

"""
from alembic import op
import sqlalchemy as sa


revision = '40990d34181d'
down_revision = '2c241b20686a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # batch-режим — совместимость ALTER с SQLite; имя FK задано явно,
    # иначе drop_constraint в downgrade не найдёт безымянный ключ.
    with op.batch_alter_table('expected_payments') as batch:
        batch.add_column(sa.Column('transaction_id', sa.Integer(), nullable=True))
        batch.create_foreign_key(
            'fk_expected_payments_transaction_id',
            'transactions', ['transaction_id'], ['id'],
        )


def downgrade() -> None:
    with op.batch_alter_table('expected_payments') as batch:
        batch.drop_constraint('fk_expected_payments_transaction_id', type_='foreignkey')
        batch.drop_column('transaction_id')
