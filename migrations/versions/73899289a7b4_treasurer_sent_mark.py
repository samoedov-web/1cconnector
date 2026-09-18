"""treasurer sent mark

Revision ID: 73899289a7b4
Revises: 40990d34181d
Create Date: 2026-07-14 04:13:03.564098

"""
from alembic import op
import sqlalchemy as sa


revision = '73899289a7b4'
down_revision = '40990d34181d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default — NOT NULL колонки на непустой таблице;
    # batch-режим — совместимость ALTER с SQLite.
    with op.batch_alter_table('expected_payments') as batch:
        batch.add_column(sa.Column('sent_marked_at', sa.DateTime(timezone=True),
                                   nullable=True))
        batch.add_column(sa.Column('sent_marked_by', sa.String(length=128),
                                   nullable=False, server_default=''))
        batch.add_column(sa.Column('sent_timeout_alerted', sa.Boolean(),
                                   nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table('expected_payments') as batch:
        batch.drop_column('sent_timeout_alerted')
        batch.drop_column('sent_marked_by')
        batch.drop_column('sent_marked_at')
