"""repair canonical stage schema

Revision ID: 82034c94d45f
Revises: 265227d646bb4a9f
Create Date: 2026-09-30 06:20:59.784630

"""
from alembic import op
import sqlalchemy as sa


revision = "82034c94d45f"
down_revision = "265227d646bb4a9f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 265227 created operations.state as VARCHAR(64) with lowercase
    # server_default='draft', while the current ORM Enum persists enum NAMES:
    # DRAFT, APPROVED, etc.
    #
    # Normalize already persisted values before aligning the column type.
    op.execute(
        sa.text(
            """
            UPDATE operations
            SET state = UPPER(state)
            WHERE state IS NOT NULL
            """
        )
    )

    # batch_alter_table keeps this migration usable with the SQLite test/dev
    # database as well as PostgreSQL.
    with op.batch_alter_table("operations") as batch:
        batch.alter_column(
            "state",
            existing_type=sa.String(length=64),
            existing_nullable=False,
            server_default=None,
        )

    op.create_table(
        "evidence_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.Integer(), nullable=False),
        sa.Column("evidence_type", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("source_reference", sa.String(length=256), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("canonical_payload", sa.Text(), nullable=False),
        sa.Column("sha256_checksum", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("adapter_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["operations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "compliance_decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.Integer(), nullable=False),
        sa.Column("decision_type", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("decided_by", sa.String(length=128), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason_note", sa.Text(), nullable=False),
        sa.Column("regulatory_version_id", sa.Integer(), nullable=True),
        sa.Column("evidence_link_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["operations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["regulatory_version_id"],
            ["regulatory_versions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["evidence_link_id"],
            ["evidence_links.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "registry_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.Integer(), nullable=False),
        sa.Column("subject_reference", sa.String(length=256), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("status_result", sa.String(length=64), nullable=False),
        sa.Column("snapshot_payload", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("regulatory_version_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["operations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["regulatory_version_id"],
            ["regulatory_versions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "issuer_risk_checks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("contract_address", sa.String(length=128), nullable=False),
        sa.Column("network_id", sa.Integer(), nullable=False),
        sa.Column("risk_status", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["operations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
        ),
        sa.ForeignKeyConstraint(
            ["network_id"],
            ["networks.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "review_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.Integer(), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("assigned_to", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["operations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "reconciliation_cases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.Integer(), nullable=False),
        sa.Column("case_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expected_reference", sa.String(length=256), nullable=False),
        sa.Column("actual_reference", sa.String(length=256), nullable=False),
        sa.Column("difference_details", sa.JSON(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["operations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("reconciliation_cases")
    op.drop_table("review_tasks")
    op.drop_table("issuer_risk_checks")
    op.drop_table("registry_snapshots")
    op.drop_table("compliance_decisions")
    op.drop_table("evidence_links")

    # Restore the schema created by 265227.
    with op.batch_alter_table("operations") as batch:
        batch.alter_column(
            "state",
            existing_type=sa.String(length=64),
            existing_nullable=False,
            server_default="draft",
        )

    op.execute(
        sa.text(
            """
            UPDATE operations
            SET state = LOWER(state)
            WHERE state IS NOT NULL
            """
        )
    )
