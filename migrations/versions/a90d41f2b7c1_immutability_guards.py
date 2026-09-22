"""Неизменяемость первички на уровне БД (п. 1.3 ТЗ), только PostgreSQL.

Триггеры запрещают:
- у transactions — изменение доказательных полей (хэш, суммы, адреса, сырой
  ответ ноды, момент получения) и любое удаление; статусные поля (status,
  confirmations, block_number, finalized_at, cross_checked, fee_*) остаются
  изменяемыми — их ведёт индексер;
- у rate_snapshots и audit_log — любые UPDATE и DELETE (append-only).

На SQLite (dev/тесты) миграция — no-op: неизменяемость принуждается кодом.

Revision ID: a90d41f2b7c1
Revises: 416c31409190
Create Date: 2026-07-10

"""
from alembic import op

revision = 'a90d41f2b7c1'
down_revision = '416c31409190'
branch_labels = None
depends_on = None

FORBID_FN = """
CREATE OR REPLACE FUNCTION connector_forbid_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Таблица % неизменяема (журнал первички/аудита)', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;
"""

TX_GUARD_FN = """
CREATE OR REPLACE FUNCTION connector_guard_transaction() RETURNS trigger AS $$
BEGIN
    IF NEW.tx_hash IS DISTINCT FROM OLD.tx_hash
       OR NEW.log_index IS DISTINCT FROM OLD.log_index
       OR NEW.network_id IS DISTINCT FROM OLD.network_id
       OR NEW.asset_id IS DISTINCT FROM OLD.asset_id
       OR NEW.amount IS DISTINCT FROM OLD.amount
       OR NEW.from_address IS DISTINCT FROM OLD.from_address
       OR NEW.to_address IS DISTINCT FROM OLD.to_address
       OR NEW.block_time IS DISTINCT FROM OLD.block_time
       OR NEW.raw_response::text IS DISTINCT FROM OLD.raw_response::text
       OR NEW.received_at IS DISTINCT FROM OLD.received_at
       OR NEW.source IS DISTINCT FROM OLD.source THEN
        RAISE EXCEPTION 'Доказательные поля транзакции % неизменяемы', OLD.tx_hash;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

TRIGGERS = [
    "CREATE TRIGGER trg_tx_guard BEFORE UPDATE ON transactions"
    " FOR EACH ROW EXECUTE FUNCTION connector_guard_transaction();",
    "CREATE TRIGGER trg_tx_no_delete BEFORE DELETE ON transactions"
    " FOR EACH ROW EXECUTE FUNCTION connector_forbid_change();",
    "CREATE TRIGGER trg_rates_immutable BEFORE UPDATE OR DELETE ON rate_snapshots"
    " FOR EACH ROW EXECUTE FUNCTION connector_forbid_change();",
    "CREATE TRIGGER trg_audit_immutable BEFORE UPDATE OR DELETE ON audit_log"
    " FOR EACH ROW EXECUTE FUNCTION connector_forbid_change();",
]

DROP = [
    "DROP TRIGGER IF EXISTS trg_tx_guard ON transactions;",
    "DROP TRIGGER IF EXISTS trg_tx_no_delete ON transactions;",
    "DROP TRIGGER IF EXISTS trg_rates_immutable ON rate_snapshots;",
    "DROP TRIGGER IF EXISTS trg_audit_immutable ON audit_log;",
    "DROP FUNCTION IF EXISTS connector_guard_transaction();",
    "DROP FUNCTION IF EXISTS connector_forbid_change();",
]


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(FORBID_FN)
    op.execute(TX_GUARD_FN)
    for trigger in TRIGGERS:
        op.execute(trigger)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for statement in DROP:
        op.execute(statement)
