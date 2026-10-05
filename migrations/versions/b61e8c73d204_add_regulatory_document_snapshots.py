"""add regulatory document snapshots and enrich registry evidence

Revision ID: b61e8c73d204
Revises: a17d9c5f0b42
"""

from alembic import op
import sqlalchemy as sa


revision = "b61e8c73d204"
down_revision = "a17d9c5f0b42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "regulatory_document_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),

        sa.Column("document_type", sa.String(length=64), nullable=False),
        sa.Column("issuer", sa.String(length=128), nullable=False),

        sa.Column("document_number", sa.String(length=128), nullable=False),
        sa.Column("document_date", sa.DateTime(timezone=True), nullable=True),

        sa.Column("registration_number", sa.String(length=128), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=True),

        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),

        sa.Column("status", sa.String(length=32), nullable=False),

        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),

        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),

        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),

        sa.Column("regulatory_version_id", sa.Integer(), nullable=True),
        sa.Column("supersedes_snapshot_id", sa.Integer(), nullable=True),

        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),

        sa.ForeignKeyConstraint(
            ["regulatory_version_id"],
            ["regulatory_versions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_snapshot_id"],
            ["regulatory_document_snapshots.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    with op.batch_alter_table("registry_snapshots") as batch:
        batch.add_column(
            sa.Column("registry_type", sa.String(length=64), nullable=True)
        )
        batch.add_column(
            sa.Column("registry_entry_id", sa.String(length=128), nullable=True)
        )

        batch.add_column(
            sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True)
        )

        batch.add_column(
            sa.Column("admission_mode", sa.String(length=32), nullable=True)
        )

        batch.add_column(
            sa.Column("application_id", sa.String(length=128), nullable=True)
        )
        batch.add_column(
            sa.Column("admission_date", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("legal_basis", sa.Text(), nullable=True)
        )

        batch.add_column(
            sa.Column("source_url", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("registry_snapshots") as batch:
        batch.drop_column("source_url")
        batch.drop_column("legal_basis")
        batch.drop_column("admission_date")
        batch.drop_column("application_id")
        batch.drop_column("admission_mode")
        batch.drop_column("valid_to")
        batch.drop_column("valid_from")
        batch.drop_column("registry_entry_id")
        batch.drop_column("registry_type")

    op.drop_table("regulatory_document_snapshots")
