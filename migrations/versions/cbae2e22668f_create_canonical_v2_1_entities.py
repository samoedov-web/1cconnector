"""create canonical v2.1 entities

Revision ID: cbae2e22668f
Revises: f8a9c3d2e1b5
Create Date: 2026-09-21

"""
from alembic import op
import sqlalchemy as sa


revision = 'cbae2e22668f'
down_revision = 'f8a9c3d2e1b5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # RegulatoryVersionStatus enum
    regulatory_version_status = sa.Enum('DRAFT', 'PUBLISHED', 'EFFECTIVE', 'SUPERSEDED', name='regulatoryversionstatus')
    regulatory_version_status.create(op.get_bind())
    
    # ComplianceDecisionType enum
    compliance_decision_type = sa.Enum('AML', 'REGISTRY', 'ISSUER', name='compliancedecisiontype')
    compliance_decision_type.create(op.get_bind())
    
    # ComplianceDecisionResult enum
    compliance_decision_result = sa.Enum('APPROVED', 'REJECTED', 'PENDING', name='compliancedecisionresult')
    compliance_decision_result.create(op.get_bind())
    
    # ReviewTaskStatus enum
    review_task_status = sa.Enum('PENDING', 'ASSIGNED', 'RESOLVED', 'CANCELLED', name='reviewtaskstatus')
    review_task_status.create(op.get_bind())
    
    # Create regulatory_versions table
    op.create_table(
        'regulatory_versions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('source_document', sa.String(length=512), nullable=False),
        sa.Column('effective_from', sa.DateTime(timezone=True), nullable=False),
        sa.Column('effective_to', sa.DateTime(timezone=True), nullable=True),
        sa.Column('checksum', sa.String(length=64), nullable=False),
        sa.Column('status', sa.Enum('DRAFT', 'PUBLISHED', 'EFFECTIVE', 'SUPERSEDED', name='regulatoryversionstatus'), nullable=False, server_default='draft'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', 'effective_from')
    )
    op.create_index('ix_regulatory_versions_code_effective', 'regulatory_versions', ['code', 'effective_from'])
    
    # Create operations table
    op.create_table(
        'operations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('invoice_id', sa.Integer(), nullable=False),
        sa.Column('contract_ref', sa.String(length=256), nullable=True),
        sa.Column('counterparty_id', sa.Integer(), nullable=True),
        sa.Column('asset_id', sa.Integer(), nullable=True),
        sa.Column('network_id', sa.Integer(), nullable=True),
        sa.Column('wallet_address', sa.String(length=128), nullable=True),
        sa.Column('regulatory_version_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id']),
        sa.ForeignKeyConstraint(['counterparty_id'], ['counterparties.id']),
        sa.ForeignKeyConstraint(['asset_id'], ['assets.id']),
        sa.ForeignKeyConstraint(['network_id'], ['networks.id']),
        sa.ForeignKeyConstraint(['regulatory_version_id'], ['regulatory_versions.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('invoice_id')
    )
    op.create_index('ix_operations_invoice_id', 'operations', ['invoice_id'])
    op.create_index('ix_operations_regulatory_version_id', 'operations', ['regulatory_version_id'])
    
    # Create evidence_links table
    op.create_table(
        'evidence_links',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('operation_id', sa.Integer(), nullable=False),
        sa.Column('evidence_type', sa.String(length=64), nullable=False),
        sa.Column('source', sa.String(length=128), nullable=False),
        sa.Column('source_reference', sa.String(length=256), nullable=True),
        sa.Column('raw_payload', sa.JSON(), nullable=True),
        sa.Column('canonical_payload', sa.JSON(), nullable=True),
        sa.Column('sha256_checksum', sa.String(length=64), nullable=False),
        sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('adapter_version', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['operation_id'], ['operations.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_evidence_links_operation_id', 'evidence_links', ['operation_id'])
    op.create_index('ix_evidence_links_source_ref', 'evidence_links', ['source', 'source_reference'])
    
    # Create compliance_decisions table
    op.create_table(
        'compliance_decisions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('operation_id', sa.Integer(), nullable=False),
        sa.Column('decision_type', sa.Enum('AML', 'REGISTRY', 'ISSUER', name='compliancedecisiontype'), nullable=False),
        sa.Column('decision', sa.Enum('APPROVED', 'REJECTED', 'PENDING', name='compliancedecisionresult'), nullable=False),
        sa.Column('decided_by', sa.String(length=128), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('reason_note', sa.Text(), nullable=True),
        sa.Column('regulatory_version_id', sa.Integer(), nullable=True),
        sa.Column('evidence_link_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['operation_id'], ['operations.id']),
        sa.ForeignKeyConstraint(['regulatory_version_id'], ['regulatory_versions.id']),
        sa.ForeignKeyConstraint(['evidence_link_id'], ['evidence_links.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_compliance_decisions_operation_id', 'compliance_decisions', ['operation_id'])
    op.create_index('ix_compliance_decisions_type_result', 'compliance_decisions', ['decision_type', 'decision'])
    
    # Create registry_snapshots table
    op.create_table(
        'registry_snapshots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('operation_id', sa.Integer(), nullable=False),
        sa.Column('subject_reference', sa.String(length=256), nullable=False),
        sa.Column('source', sa.String(length=128), nullable=False),
        sa.Column('status_result', sa.String(length=64), nullable=False),
        sa.Column('snapshot_payload', sa.JSON(), nullable=True),
        sa.Column('checksum', sa.String(length=64), nullable=False),
        sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('regulatory_version_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['operation_id'], ['operations.id']),
        sa.ForeignKeyConstraint(['regulatory_version_id'], ['regulatory_versions.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_registry_snapshots_operation_id', 'registry_snapshots', ['operation_id'])
    op.create_index('ix_registry_snapshots_subject', 'registry_snapshots', ['subject_reference'])
    
    # Create issuer_risk_checks table
    op.create_table(
        'issuer_risk_checks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('operation_id', sa.Integer(), nullable=False),
        sa.Column('asset_id', sa.Integer(), nullable=False),
        sa.Column('contract_address', sa.String(length=128), nullable=True),
        sa.Column('network_id', sa.Integer(), nullable=True),
        sa.Column('risk_status', sa.String(length=64), nullable=False),
        sa.Column('source', sa.String(length=128), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=True),
        sa.Column('checksum', sa.String(length=64), nullable=False),
        sa.Column('checked_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['operation_id'], ['operations.id']),
        sa.ForeignKeyConstraint(['asset_id'], ['assets.id']),
        sa.ForeignKeyConstraint(['network_id'], ['networks.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_issuer_risk_checks_operation_id', 'issuer_risk_checks', ['operation_id'])
    op.create_index('ix_issuer_risk_checks_asset_id', 'issuer_risk_checks', ['asset_id'])
    
    # Create review_tasks table
    op.create_table(
        'review_tasks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('operation_id', sa.Integer(), nullable=False),
        sa.Column('task_type', sa.String(length=64), nullable=False),
        sa.Column('status', sa.Enum('PENDING', 'ASSIGNED', 'RESOLVED', 'CANCELLED', name='reviewtaskstatus'), nullable=False, server_default='pending'),
        sa.Column('priority', sa.String(length=16), nullable=False, server_default='normal'),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('assigned_to', sa.String(length=128), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolution_note', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['operation_id'], ['operations.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_review_tasks_operation_id', 'review_tasks', ['operation_id'])
    op.create_index('ix_review_tasks_status_priority', 'review_tasks', ['status', 'priority'])
    
    # Create reconciliation_cases table
    op.create_table(
        'reconciliation_cases',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('operation_id', sa.Integer(), nullable=False),
        sa.Column('case_type', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='open'),
        sa.Column('expected_reference', sa.String(length=256), nullable=True),
        sa.Column('actual_reference', sa.String(length=256), nullable=True),
        sa.Column('difference_details', sa.JSON(), nullable=True),
        sa.Column('opened_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolution', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['operation_id'], ['operations.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_reconciliation_cases_operation_id', 'reconciliation_cases', ['operation_id'])
    op.create_index('ix_reconciliation_cases_status', 'reconciliation_cases', ['status'])


def downgrade() -> None:
    op.drop_table('reconciliation_cases')
    op.drop_table('review_tasks')
    op.drop_table('issuer_risk_checks')
    op.drop_table('registry_snapshots')
    op.drop_table('compliance_decisions')
    op.drop_table('evidence_links')
    op.drop_table('operations')
    op.drop_table('regulatory_versions')
    
    # Drop enums
    sa.Enum(name='reviewtaskstatus').drop(op.get_bind())
    sa.Enum(name='compliancedecisionresult').drop(op.get_bind())
    sa.Enum(name='compliancedecisiontype').drop(op.get_bind())
    sa.Enum(name='regulatoryversionstatus').drop(op.get_bind())
