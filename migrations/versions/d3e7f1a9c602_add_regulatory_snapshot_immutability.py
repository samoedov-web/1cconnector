"""add regulatory snapshot immutability

Regulatory evidence is append-only on PostgreSQL:
- regulatory_document_snapshots: UPDATE/DELETE forbidden;
- registry_snapshots: UPDATE/DELETE forbidden.

Repeated checks must create new snapshot rows instead of mutating history.

SQLite/dev remains a migration no-op, consistent with the existing
immutability strategy used by rate snapshots, audit log, AML and custody.

Revision ID: d3e7f1a9c602
Revises: b61e8c73d204
"""

from alembic import op


revision = "d3e7f1a9c602"
down_revision = "b61e8c73d204"
branch_labels = None
depends_on = None


TRIGGERS = [
    (
        "CREATE TRIGGER trg_regulatory_document_snapshots_immutable "
        "BEFORE UPDATE OR DELETE ON regulatory_document_snapshots "
        "FOR EACH ROW EXECUTE FUNCTION connector_forbid_change();"
    ),
    (
        "CREATE TRIGGER trg_registry_snapshots_immutable "
        "BEFORE UPDATE OR DELETE ON registry_snapshots "
        "FOR EACH ROW EXECUTE FUNCTION connector_forbid_change();"
    ),
]

DROP = [
    (
        "DROP TRIGGER IF EXISTS trg_registry_snapshots_immutable "
        "ON registry_snapshots;"
    ),
    (
        "DROP TRIGGER IF EXISTS trg_regulatory_document_snapshots_immutable "
        "ON regulatory_document_snapshots;"
    ),
]


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    for trigger in TRIGGERS:
        op.execute(trigger)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    for statement in DROP:
        op.execute(statement)
