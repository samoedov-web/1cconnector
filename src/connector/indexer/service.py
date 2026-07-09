"""Сервис индексации: опрос адаптеров, статусы финальности, дедупликация.

Жизненный цикл транзакции (п. 3 ТЗ):

    seen → confirmed(N) → final          (final — терминальный статус)
    seen/confirmed → orphaned            (реорг: транзакция выпала из цепочки)

Порог N — настройка per-network (Network.finality_depth). Финальная
транзакция больше никогда не меняется; на момент финальности фиксируется
снимок курса и создаётся партия ФИФО / документ выбытия.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from connector.models import (
    Asset,
    Direction,
    Network,
    Transaction,
    TxStatus,
    Wallet,
    utcnow,
)
from connector.indexer.base import RawTransfer


@dataclass(frozen=True)
class StatusChange:
    status: TxStatus
    confirmations: int


def resolve_status(
    tx_block: int, latest_block: int, finality_depth: int, present_in_chain: bool = True
) -> StatusChange:
    """Чистая функция перехода статусов — тестируется без БД.

    present_in_chain=False означает, что при перечитывании транзакция не
    найдена в канонической цепочке (реорг).
    """
    if not present_in_chain:
        return StatusChange(TxStatus.ORPHANED, 0)
    confirmations = max(0, latest_block - tx_block + 1)
    if confirmations >= finality_depth:
        return StatusChange(TxStatus.FINAL, confirmations)
    if confirmations >= 1:
        return StatusChange(TxStatus.CONFIRMED, confirmations)
    return StatusChange(TxStatus.SEEN, 0)


def direction_for(transfer: RawTransfer, wallet_address: str) -> Direction:
    if transfer.to_address.lower() == wallet_address.lower():
        return Direction.IN
    return Direction.OUT


@dataclass(frozen=True)
class MergedTransfer:
    """Трансфер после сверки источников: cross_checked — виден минимум двум."""

    transfer: RawTransfer
    cross_checked: bool


def merge_sources(per_source: list[list[RawTransfer]]) -> list[MergedTransfer]:
    """Объединить выдачу нескольких источников (защита от неполных данных).

    Берётся объединение множеств: транзакция, которую отдал хотя бы один
    источник, не теряется; подтверждённая двумя и более помечается
    cross_checked. Расхождения наборов логирует вызывающий код.
    """
    seen: dict[tuple[str, int], MergedTransfer] = {}
    for transfers in per_source:
        for t in transfers:
            key = (t.tx_hash, t.log_index)
            if key in seen:
                seen[key] = MergedTransfer(seen[key].transfer, cross_checked=True)
            else:
                seen[key] = MergedTransfer(t, cross_checked=False)
    return list(seen.values())


class IndexerService:
    """Оркестрация одного цикла опроса для одного кошелька."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def ingest_transfers(
        self,
        wallet: Wallet,
        network: Network,
        transfers: list[MergedTransfer],
        latest_block: int,
    ) -> list[Transaction]:
        """Сохранить новые трансферы; дедупликация по (network, hash, log_index)."""
        created: list[Transaction] = []
        assets = {
            a.contract_address.lower(): a
            for a in (
                await self.session.execute(
                    select(Asset).where(Asset.network_id == network.id)
                )
            ).scalars()
        }
        for merged in transfers:
            t = merged.transfer
            asset = assets.get(t.token_contract.lower())
            if asset is None:
                continue
            existing = await self.session.scalar(
                select(Transaction).where(
                    Transaction.network_id == network.id,
                    Transaction.tx_hash == t.tx_hash,
                    Transaction.log_index == t.log_index,
                )
            )
            if existing is not None:
                # Второй источник подтвердил уже сохранённую транзакцию.
                if merged.cross_checked and not existing.cross_checked:
                    existing.cross_checked = True
                continue
            change = resolve_status(t.block_number, latest_block, network.finality_depth)
            tx = Transaction(
                network_id=network.id,
                asset_id=asset.id,
                wallet_id=wallet.id,
                tx_hash=t.tx_hash,
                log_index=t.log_index,
                block_number=t.block_number,
                block_time=t.block_time,
                direction=direction_for(t, wallet.address),
                from_address=t.from_address,
                to_address=t.to_address,
                amount=t.amount,
                fee_amount=t.fee_amount,
                fee_asset=t.fee_asset,
                status=change.status,
                confirmations=change.confirmations,
                finalized_at=utcnow() if change.status == TxStatus.FINAL else None,
                raw_response=t.raw,
                source=t.source,
                cross_checked=merged.cross_checked,
            )
            self.session.add(tx)
            created.append(tx)
        await self.session.flush()
        return created

    async def check_reorgs(self, network: Network, adapter) -> list[Transaction]:
        """Перепроверить нефинальные транзакции в канонической цепочке.

        Выпавшая из цепочки помечается orphaned (учёт её не увидит),
        переехавшая в другой блок получает новый номер — подтверждения
        пересчитает advance_finality. Финальные транзакции не трогаются:
        порог N выбран так, что реорг глубже него — событие уровня сети.
        """
        pending = (
            (
                await self.session.execute(
                    select(Transaction).where(
                        Transaction.network_id == network.id,
                        Transaction.status.in_([TxStatus.SEEN, TxStatus.CONFIRMED]),
                    )
                )
            )
            .scalars()
            .all()
        )
        changed: list[Transaction] = []
        for tx in pending:
            block = await adapter.get_transaction_block(tx.tx_hash)
            if block is None:
                tx.status = TxStatus.ORPHANED
                tx.confirmations = 0
                changed.append(tx)
            elif block != tx.block_number:
                tx.block_number = block
                changed.append(tx)
        await self.session.flush()
        return changed

    async def advance_finality(self, network: Network, latest_block: int) -> list[Transaction]:
        """Продвинуть незафинализированные транзакции сети по подтверждениям."""
        pending = (
            await self.session.execute(
                select(Transaction).where(
                    Transaction.network_id == network.id,
                    Transaction.status.in_([TxStatus.SEEN, TxStatus.CONFIRMED]),
                )
            )
        ).scalars()
        finalized: list[Transaction] = []
        for tx in pending:
            change = resolve_status(tx.block_number, latest_block, network.finality_depth)
            tx.confirmations = change.confirmations
            if change.status != tx.status:
                tx.status = change.status
                if change.status == TxStatus.FINAL:
                    tx.finalized_at = utcnow()
                    finalized.append(tx)
        await self.session.flush()
        return finalized
