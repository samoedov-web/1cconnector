"""add expected_tx_hash for deterministic matching

Revision ID: f8a9c3d2e1b5
Revises: 73899289a7b4
Create Date: 2026-09-18

"""
from alembic import op
import sqlalchemy as sa


revision = 'f8a9c3d2e1b5'
down_revision = '73899289a7b4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('expected_payments') as batch:
        batch.add_column(sa.Column('expected_tx_hash', sa.String(length=128), nullable=True))
    
    op.create_index('ix_expected_payments_expected_tx_hash', 'expected_payments', ['expected_tx_hash'])


def downgrade() -> None:
    op.drop_index('ix_expected_payments_expected_tx_hash', table_name='expected_payments')
    with op.batch_alter_table('expected_payments') as batch:
        batch.drop_column('expected_tx_hash')
