"""add participant-level registry monitoring

Adds a separate operational monitoring layer for registry participants.

Important boundary:
- registry_snapshots remain operation-scoped regulatory evidence;
- registry_monitoring_targets are mutable scheduler configuration/state;
- registry_monitoring_snapshots are append-only historical observations.

Revision ID: e4c9b7a2d105
Revises: d3e7f1a9c602
"""

from alembic import op
import sqlalchemy as sa


revision = "e4c9b7a2d105"
down_revision = "d3e7f1a9c602"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "registry_monitoring_targets",
        sa.Column("id", sa.Integer(), nullable=False),

        sa.Column("subject_reference", sa.String(length=256), nullable=False),
        sa.Column("registry_type", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),

        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("refresh_interval_seconds", sa.Integer(), nullable=False),

        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True),

        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),

        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "subject_reference",
            "registry_type",
            "source_id",
            name="uq_registry_monitoring_target_subject_type_source",
        ),
    )

    op.create_table(
        "registry_monitoring_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),

        sa.Column("target_id", sa.Integer(), nullable=False),

        sa.Column("subject_reference", sa.String(length=256), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("status_result", sa.String(length=64), nullable=False),

        sa.Column("snapshot_payload", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),

        sa.Column("registry_type", sa.String(length=64), nullable=True),
        sa.Column("registry_entry_id", sa.String(length=128), nullable=True),

        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),

        sa.Column("admission_mode", sa.String(length=32), nullable=True),

        sa.Column("application_id", sa.String(length=128), nullable=True),
        sa.Column("admission_date", sa.DateTime(timezone=True), nullable=True),

        sa.Column("legal_basis", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),

        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),

        sa.ForeignKeyConstraint(
            ["target_id"],
            ["registry_monitoring_targets.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE TRIGGER trg_registry_monitoring_snapshots_immutable "
            "BEFORE UPDATE OR DELETE ON registry_monitoring_snapshots "
            "FOR EACH ROW EXECUTE FUNCTION connector_forbid_change();"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS "
            "trg_registry_monitoring_snapshots_immutable "
            "ON registry_monitoring_snapshots;"
        )

    op.drop_table("registry_monitoring_snapshots")
    op.drop_table("registry_monitoring_targets")
