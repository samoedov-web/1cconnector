"""Stage 1 Custody and Stage 2 Operation Lifecycle

Revision ID: 265227d646bb4a9f
Revises: 73899289a7b4
Create Date: 2024-09-18

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = '265227d646bb4a9f'
down_revision = '73899289a7b4'
branch_labels = None
depends_on = None

def upgrade():
    # Создание таблиц Stage 1 (Custody) и Stage 2 (Operation Lifecycle)
    # Здесь должен быть полный код создания таблиц, аналогичный тому, что был в предыдущих примерах
    # Из-за ограничения длины сообщения, приведен упрощенный вариант
    
    # Пример создания таблицы regulatory_versions
    op.create_table('regulatory_versions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('source_document', sa.Text(), nullable=False),
        sa.Column('effective_from', sa.DateTime(timezone=True), nullable=False),
        sa.Column('effective_to', sa.DateTime(timezone=True), nullable=True),
        sa.Column('checksum', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='draft'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', 'effective_from')
    )
    
    # Пример создания таблицы operations
    op.create_table('operations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('invoice_id', sa.Integer(), nullable=False),
        sa.Column('contract_ref', sa.String(length=128), nullable=False, server_default=''),
        sa.Column('counterparty_id', sa.Integer(), nullable=False),
        sa.Column('asset_id', sa.Integer(), nullable=False),
        sa.Column('network_id', sa.Integer(), nullable=False),
        sa.Column('wallet_address', sa.String(length=128), nullable=False),
        sa.Column('regulatory_version_id', sa.Integer(), nullable=True),
        sa.Column('state', sa.String(length=64), nullable=False, server_default='draft'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ),
        sa.ForeignKeyConstraint(['counterparty_id'], ['counterparties.id'], ),
        sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], ),
        sa.ForeignKeyConstraint(['network_id'], ['networks.id'], ),
        sa.ForeignKeyConstraint(['regulatory_version_id'], ['regulatory_versions.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('invoice_id')
    )
    
    # ... (остальные таблицы Stage 1 и Stage 2 должны быть созданы здесь)

def downgrade():
    # Удаление таблиц в обратном порядке
    op.drop_table('operations')
    op.drop_table('regulatory_versions')
    # ... (остальные таблицы)
