"""Генератор фикстур «выписки депозитария» из реальных chain-данных (п. 4.4).

Берёт финальные транзакции за период из БД коннектора и производит выписку
с конфигурируемыми искажениями — для тестирования движка сверки на всех
сценариях раздела 6 спеки:

- drop N        — потерять N записей (недостача в выписке);
- extra N       — добавить N записей, которых нет в цепочке;
- amount_noise  — исказить суммы (комиссия удержана внутри суммы);
- date_shift D  — сдвинуть даты части записей на ±D дней (в т.ч. через
                  границу периода);
- aggregate     — day|counterparty: одна строка = несколько транзакций;
- strip         — hashes|counterparty|network: эмуляция бедной выписки.

Детерминированность: одинаковый seed → одинаковая фикстура.
"""

from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from connector.custody.store import EntryPayload
from connector.models import CustodyOperationType, Direction, Transaction, TxStatus


@dataclass(frozen=True)
class DistortionOptions:
    drop: int = 0
    extra: int = 0
    amount_noise: bool = False
    date_shift_days: int = 0
    aggregate: str | None = None  # day | counterparty
    strip: tuple[str, ...] = field(default=())  # hashes | counterparty | network


async def chain_entries_for_period(
    session: AsyncSession, period_from: datetime, period_to: datetime
) -> list[EntryPayload]:
    """Идеальная выписка: по одной строке на финальную транзакцию периода."""
    txs = (
        (
            await session.execute(
                select(Transaction)
                .options(
                    selectinload(Transaction.network),
                    selectinload(Transaction.asset),
                )
                .where(
                    Transaction.status == TxStatus.FINAL,
                    Transaction.block_time >= period_from,
                    Transaction.block_time <= period_to,
                )
                .order_by(Transaction.block_time, Transaction.id)
            )
        )
        .scalars()
        .all()
    )
    entries = []
    for i, tx in enumerate(txs, start=1):
        incoming = tx.direction == Direction.IN
        entries.append(
            EntryPayload(
                entry_id=str(i),
                occurred_at=tx.block_time,
                asset=tx.asset.symbol,
                amount=tx.amount if incoming else -tx.amount,
                operation_type=(
                    CustodyOperationType.DEPOSIT if incoming
                    else CustodyOperationType.WITHDRAWAL
                ),
                network=tx.network.code,
                counterparty_ref=tx.from_address if incoming else tx.to_address,
                external_tx_hash=tx.tx_hash,
            )
        )
    return entries


def distort(
    entries: list[EntryPayload], options: DistortionOptions, seed: int = 0
) -> list[EntryPayload]:
    rng = random.Random(seed)
    result = list(entries)

    if options.drop:
        for _ in range(min(options.drop, len(result))):
            result.pop(rng.randrange(len(result)))

    if options.extra:
        for i in range(options.extra):
            result.append(
                EntryPayload(
                    entry_id=f"EXTRA-{i + 1}",
                    occurred_at=entries[0].occurred_at if entries else datetime.now().astimezone(),
                    asset="USDT",
                    amount=Decimal(rng.randrange(100, 5000)),
                    operation_type=CustodyOperationType.DEPOSIT,
                    counterparty_ref=f"unknown-{i + 1}",
                    external_tx_hash=f"extra-{seed}-{i + 1}",
                )
            )

    if options.amount_noise:
        # «Комиссия удержана внутри суммы»: уменьшаем модуль ~половины строк
        # на 0.1–0.5%.
        noisy = []
        for entry in result:
            if rng.random() < 0.5:
                factor = Decimal(1) - Decimal(rng.randrange(10, 50)) / Decimal(10_000)
                entry = replace(entry, amount=entry.amount * factor)
            noisy.append(entry)
        result = noisy

    if options.date_shift_days:
        # Сдвигаем ~20% строк на ±D дней — часть уедет за границу периода.
        shifted = []
        for entry in result:
            if rng.random() < 0.2:
                days = rng.choice([-options.date_shift_days, options.date_shift_days])
                entry = replace(entry, occurred_at=entry.occurred_at + timedelta(days=days))
            shifted.append(entry)
        result = shifted

    if options.aggregate:
        result = _aggregate(result, by=options.aggregate)

    if options.strip:
        stripped = []
        for entry in result:
            if "hashes" in options.strip:
                entry = replace(entry, external_tx_hash=None)
            if "counterparty" in options.strip:
                entry = replace(entry, counterparty_ref=None)
            if "network" in options.strip:
                entry = replace(entry, network=None)
            stripped.append(entry)
        result = stripped

    return result


def _aggregate(entries: list[EntryPayload], by: str) -> list[EntryPayload]:
    """Одна строка выписки = несколько транзакций (granularity=aggregated)."""
    groups: dict[tuple, list[EntryPayload]] = {}
    for entry in entries:
        direction = "in" if entry.amount >= 0 else "out"
        if by == "day":
            key = (entry.asset, direction, entry.occurred_at.date())
        elif by == "counterparty":
            key = (entry.asset, direction, entry.counterparty_ref)
        else:
            raise ValueError(f"Неизвестный вид агрегации: {by}")
        groups.setdefault(key, []).append(entry)

    aggregated = []
    for i, (key, group) in enumerate(sorted(groups.items(), key=lambda kv: str(kv[0])), 1):
        total = sum((e.amount for e in group), Decimal(0))
        aggregated.append(
            EntryPayload(
                entry_id=f"AGG-{i}",
                occurred_at=max(e.occurred_at for e in group),
                asset=group[0].asset,
                amount=total,
                operation_type=(
                    CustodyOperationType.DEPOSIT if total >= 0
                    else CustodyOperationType.WITHDRAWAL
                ),
                network=group[0].network,
                counterparty_ref=group[0].counterparty_ref if by == "counterparty" else None,
                external_tx_hash=None,  # агрегат не несёт хэша
                raw_line={"aggregated_of": len(group)},
            )
        )
    return aggregated


# --- Запись фикстуры ----------------------------------------------------------


def _entry_dict(entry: EntryPayload) -> dict:
    return {
        "entry_id": entry.entry_id,
        "occurred_at": entry.occurred_at.isoformat(),
        "asset": entry.asset,
        "network": entry.network,
        "amount": str(entry.amount),
        "operation_type": entry.operation_type.value,
        "counterparty_ref": entry.counterparty_ref,
        "external_tx_hash": entry.external_tx_hash,
    }


CSV_COLUMNS = [
    "statement_id", "period_from", "period_to", "issued_at", "granularity",
    "entry_id", "occurred_at", "asset", "network", "amount", "operation_type",
    "counterparty_ref", "external_tx_hash",
]


def write_fixture(
    path: str | Path,
    statement_id: str,
    period_from: datetime,
    period_to: datetime,
    entries: list[EntryPayload],
    issued_at: datetime | None = None,
    granularity: str = "per_tx",
) -> Path:
    """Записать фикстуру; формат по расширению файла (.json / .csv)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "statement_id": statement_id,
        "period_from": period_from.isoformat(),
        "period_to": period_to.isoformat(),
        "issued_at": issued_at.isoformat() if issued_at else None,
        "granularity": granularity,
    }
    if path.suffix == ".json":
        document = meta | {"entries": [_entry_dict(e) for e in entries]}
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    elif path.suffix == ".csv":
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            for entry in entries:
                writer.writerow(meta | _entry_dict(entry))
    else:
        raise ValueError(f"Неизвестный формат фикстуры: {path.suffix} (нужен .json или .csv)")
    return path
