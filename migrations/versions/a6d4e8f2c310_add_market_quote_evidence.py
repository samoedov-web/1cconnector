"""add immutable market quote evidence

MarketQuoteEvidence preserves externally observed market quotation material
separately from accounting RateSnapshot.

Revision ID: a6d4e8f2c310
Revises: f5a1c3d7e209
"""

from alembic import op
import sqlalchemy as sa


revision = "a6d4e8f2c310"
down_revision = "f5a1c3d7e209"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_quote_evidence",
        sa.Column("id", sa.Integer(), nullable=False),

        sa.Column("base_asset", sa.String(length=32), nullable=False),
        sa.Column("quote_currency", sa.String(length=16), nullable=False),

        sa.Column("rate", sa.Numeric(38, 18), nullable=False),
        sa.Column(
            "quote_as_of",
            sa.DateTime(timezone=True),
            nullable=False,
        ),

        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),

        sa.Column(
            "methodology_version",
            sa.String(length=64),
            nullable=False,
        ),

        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),

        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),

        sa.PrimaryKeyConstraint("id"),
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE TRIGGER trg_market_quote_evidence_immutable "
            "BEFORE UPDATE OR DELETE ON market_quote_evidence "
            "FOR EACH ROW EXECUTE FUNCTION connector_forbid_change();"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS "
            "trg_market_quote_evidence_immutable "
            "ON market_quote_evidence;"
        )

    op.drop_table("market_quote_evidence")
