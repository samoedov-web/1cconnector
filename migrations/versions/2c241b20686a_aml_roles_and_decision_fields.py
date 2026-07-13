"""aml roles and decision fields

Revision ID: 2c241b20686a
Revises: 2243a1fabec4
Create Date: 2026-07-13 06:31:13.401263

"""
from alembic import op
import sqlalchemy as sa


revision = '2c241b20686a'
down_revision = '2243a1fabec4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default — колонки NOT NULL на непустой таблице; batch-режим —
    # совместимость ALTER с SQLite. Роль расширяется новыми значениями
    # (compliance длиннее прежнего максимума VARCHAR).
    with op.batch_alter_table('expected_payments') as batch:
        batch.add_column(sa.Column('decided_by', sa.String(length=128),
                                   nullable=False, server_default=''))
        batch.add_column(sa.Column('decision_note', sa.Text(),
                                   nullable=False, server_default=''))
    with op.batch_alter_table('users') as batch:
        batch.alter_column('role', existing_type=sa.VARCHAR(length=8),
                           type_=sa.Enum('ADMIN', 'OPERATOR', 'AUDITOR', 'COMPLIANCE', 'TREASURER', name='role', native_enum=False), existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.alter_column('role', existing_type=sa.Enum('ADMIN', 'OPERATOR', 'AUDITOR', 'COMPLIANCE', 'TREASURER', name='role', native_enum=False),
                           type_=sa.VARCHAR(length=8), existing_nullable=False)
    with op.batch_alter_table('expected_payments') as batch:
        batch.drop_column('decision_note')
        batch.drop_column('decided_by')
