"""Сохранение выписок депозитария в журнал неизменяемости (фаза 2).

Правила:
- сырой payload выписки сохраняется как получен, checksum — SHA-256
  канонизированного JSON (как для ответов нод);
- идемпотентность: повторная загрузка той же выписки (source_id,
  statement_id) не создаёт дублей; если при этом checksum отличается —
  это не дубль, а конфликт первички: ошибка, существующая запись
  не перезаписывается никогда;
- строки выписки пишутся вместе с выпиской одной транзакцией.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from connector.hashing import canonical_sha256
from connector.models import CustodyEntry, CustodyOperationType, CustodyStatement

log = logging.getLogger("connector.custody")


class StatementConflictError(Exception):
    """Выписка с тем же id уже сохранена, но содержимое отличается."""


@dataclass(frozen=True)
class StatementPayload:
    """Выписка, как её отдаёт адаптер (нормализованная, до сохранения)."""

    statement_id: str
    source_id: str
    period_from: datetime
    period_to: datetime
    issued_at: datetime | None
    raw_payload: dict


@dataclass(frozen=True)
class EntryPayload:
    entry_id: str
    occurred_at: datetime
    asset: str
    amount: Decimal  # знак = направление
    operation_type: CustodyOperationType
    network: str | None = None
    counterparty_ref: str | None = None
    external_tx_hash: str | None = None
    raw_line: dict = field(default_factory=dict)


async def store_statement(
    session: AsyncSession,
    statement: StatementPayload,
    entries: list[EntryPayload],
) -> CustodyStatement:
    """Сохранить выписку со строками; повторная загрузка идемпотентна."""
    checksum = canonical_sha256(statement.raw_payload)
    existing = await session.scalar(
        select(CustodyStatement).where(
            CustodyStatement.source_id == statement.source_id,
            CustodyStatement.statement_id == statement.statement_id,
        )
    )
    if existing is not None:
        if existing.checksum != checksum:
            raise StatementConflictError(
                f"Выписка {statement.source_id}:{statement.statement_id} уже "
                f"сохранена с другим содержимым (checksum {existing.checksum} != "
                f"{checksum}). Первичка неизменяема — новая версия выписки "
                "должна иметь новый statement_id."
            )
        log.info(
            "Выписка %s:%s уже загружена — пропуск",
            statement.source_id,
            statement.statement_id,
        )
        return existing

    row = CustodyStatement(
        statement_id=statement.statement_id,
        source_id=statement.source_id,
        period_from=statement.period_from,
        period_to=statement.period_to,
        issued_at=statement.issued_at,
        raw_payload=statement.raw_payload,
        checksum=checksum,
    )
    session.add(row)
    await session.flush()
    for entry in entries:
        session.add(
            CustodyEntry(
                entry_id=entry.entry_id,
                statement_pk=row.id,
                occurred_at=entry.occurred_at,
                asset=entry.asset,
                network=entry.network,
                amount=entry.amount,
                operation_type=entry.operation_type,
                counterparty_ref=entry.counterparty_ref,
                external_tx_hash=entry.external_tx_hash,
                raw_line=entry.raw_line,
            )
        )
    await session.flush()
    log.info(
        "Выписка %s:%s сохранена: строк %d, период %s—%s",
        statement.source_id,
        statement.statement_id,
        len(entries),
        statement.period_from.date(),
        statement.period_to.date(),
    )
    return row
