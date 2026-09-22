"""organizations

Revision ID: ed3d85569af8
Revises: a90d41f2b7c1
Create Date: 2026-07-10 06:38:01.206041

"""
from alembic import op
import sqlalchemy as sa


revision = 'ed3d85569af8'
down_revision = 'a90d41f2b7c1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('organizations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=512), nullable=False),
    sa.Column('inn', sa.String(length=12), nullable=False),
    sa.Column('onec_ref', sa.String(length=64), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    # batch-режим — совместимость с SQLite (ALTER ADD CONSTRAINT не поддерживается)
    with op.batch_alter_table('wallets') as batch:
        batch.add_column(sa.Column('organization_id', sa.Integer(), nullable=True))
        batch.create_foreign_key(
            'fk_wallets_organization', 'organizations', ['organization_id'], ['id']
        )


def downgrade() -> None:
    with op.batch_alter_table('wallets') as batch:
        batch.drop_constraint('fk_wallets_organization', type_='foreignkey')
        batch.drop_column('organization_id')
    op.drop_table('organizations')
