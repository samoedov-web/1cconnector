"""Конвейер обработки финализированной транзакции.

Связка контуров (п. 3–6, 8 ТЗ):

    final-транзакция
      → матчинг (правила 1–3; нераспознанное — в очередь разбора и СТОП)
      → снимок курса на момент финальности (актив → валюта контракта → RUB)
      → входящая:  партия ФИФО + проект «Поступление цифровой валюты»
        исходящая: списание ФИФО + проект «Выбытие цифровой валюты»
      → комиссия сети → проект «Комиссия» (если комиссию удалось оценить)

Свойства:
- идемпотентность: повторный прогон той же транзакции не создаёт дублей
  (проверка по idempotency_key документа и по существующим Match/Lot);
- транзакция с pending-матчем не порождает документов — они появятся в
  следующем цикле после ручного разбора оператором;
- при нехватке остатка по партиям (расхождение данных) выбытие не
  проводится и будет повторено после устранения причины.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from connector.accounting.documents import (
    build_disposal,
    build_fee,
    build_receipt,
    idempotency_key,
)
from connector.accounting.fifo import (
    CostBasisMethod,
    InsufficientBalanceError,
    LotView,
    get_method,
)
from connector.aml.flow import aml_summary, mark_matched
from connector.config import settings
from connector.matching.engine import InvoiceView, TxView, match_transaction
from connector.models import (
    Contract,
    Counterparty,
    CounterpartyAddress,
    Direction,
    DisposalLine,
    Invoice,
    InvoiceStatus,
    Lot,
    Match,
    MatchState,
    Network,
    OnecDocType,
    OnecDocument,
    RateSnapshot,
    Transaction,
    TxStatus,
)
from connector.rates.service import RateService

log = logging.getLogger("connector.pipeline")


def apply_payment_to_invoice(invoice: Invoice, amount: Decimal) -> None:
    """Учесть оплату в инвойсе (вызывается и из ручного разбора)."""
    invoice.paid_amount = (invoice.paid_amount or Decimal(0)) + amount
    if invoice.paid_amount >= invoice.amount:
        invoice.status = InvoiceStatus.PAID
    else:
        invoice.status = InvoiceStatus.PARTIALLY_PAID


class TransactionPipeline:
    def __init__(
        self,
        session: AsyncSession,
        rate_service: RateService,
        amount_tolerance: Decimal | None = None,
        cost_method: CostBasisMethod | None = None,
    ) -> None:
        self.session = session
        self.rates = rate_service
        self.tolerance = amount_tolerance or Decimal(settings.matching_amount_tolerance)
        self.cost_method = cost_method or get_method("fifo")

    # --- Публичный вход ------------------------------------------------

    async def process_network(self, network: Network) -> list[OnecDocument]:
        """Обработать все final-транзакции сети, не имеющие документов.

        Скан каждый цикл покрывает и восстановление после сбоя, и
        транзакции, разобранные оператором вручную с прошлого цикла.
        """
        doc_types = [OnecDocType.RECEIPT, OnecDocType.DISPOSAL]
        with_docs = select(OnecDocument.transaction_id).where(
            OnecDocument.doc_type.in_(doc_types), OnecDocument.transaction_id.is_not(None)
        )
        txs = (
            (
                await self.session.execute(
                    select(Transaction)
                    .options(
                        selectinload(Transaction.network),
                        selectinload(Transaction.asset),
                        selectinload(Transaction.wallet),
                    )
                    .where(
                        Transaction.network_id == network.id,
                        Transaction.status == TxStatus.FINAL,
                        Transaction.id.not_in(with_docs),
                    )
                    .order_by(Transaction.block_number, Transaction.log_index)
                )
            )
            .scalars()
            .all()
        )
        created: list[OnecDocument] = []
        for tx in txs:
            created.extend(await self.process(tx))
        return created

    async def process(self, tx: Transaction) -> list[OnecDocument]:
        """Провести одну final-транзакцию через матчинг, курсы и учёт."""
        main_type = (
            OnecDocType.RECEIPT if tx.direction == Direction.IN else OnecDocType.DISPOSAL
        )
        key = idempotency_key(tx, main_type, tx.network.code)
        exists = await self.session.scalar(
            select(OnecDocument.id).where(OnecDocument.idempotency_key == key)
        )
        if exists is not None:
            return []

        matches = await self._ensure_matches(tx)
        if any(m.state == MatchState.PENDING for m in matches):
            return []  # ждём ручного разбора

        rate = await self._ensure_rate_snapshot(tx, matches)
        allocations = await self._allocations_for_1c(matches)

        docs: list[OnecDocument] = []
        if tx.direction == Direction.IN:
            docs.append(await self._process_receipt(tx, rate, allocations, key))
        else:
            disposal = await self._process_disposal(tx, rate, allocations, key)
            if disposal is None:
                return []  # нехватка остатка — повтор в следующем цикле
            docs.append(disposal)

        fee_doc = await self._process_fee(tx)
        if fee_doc is not None:
            docs.append(fee_doc)
        await self.session.flush()
        return docs

    # --- Матчинг ---------------------------------------------------------

    async def _ensure_matches(self, tx: Transaction) -> list[Match]:
        existing = (
            (await self.session.execute(select(Match).where(Match.transaction_id == tx.id)))
            .scalars()
            .all()
        )
        if existing:
            return list(existing)

        address_book = {
            addr.address.lower(): addr.counterparty_id
            for addr in (
                await self.session.execute(
                    select(CounterpartyAddress).where(
                        CounterpartyAddress.network_id == tx.network_id
                    )
                )
            ).scalars()
        }
        invoice_rows = (
            await self.session.execute(
                select(Invoice, Contract)
                .join(Contract, Invoice.contract_id == Contract.id)
                .where(Invoice.status.in_([InvoiceStatus.OPEN, InvoiceStatus.PARTIALLY_PAID]))
            )
        ).all()
        open_invoices = [
            InvoiceView(
                id=inv.id,
                contract_id=contract.id,
                counterparty_id=contract.counterparty_id,
                currency=inv.currency,
                open_amount=inv.amount - (inv.paid_amount or Decimal(0)),
                due_from=inv.due_from,
                due_to=inv.due_to,
            )
            for inv, contract in invoice_rows
        ]
        counterparty_address = (
            tx.from_address if tx.direction == Direction.IN else tx.to_address
        )
        outcome = match_transaction(
            TxView(
                id=tx.id,
                counterparty_address=counterparty_address,
                amount=tx.amount,
                asset_symbol=tx.asset.symbol,
                block_time=tx.block_time,
            ),
            address_book,
            open_invoices,
            amount_tolerance=self.tolerance,
        )

        matches: list[Match] = []
        for alloc in outcome.allocations:
            pending = alloc.invoice_id is None and outcome.needs_manual_review
            match = Match(
                transaction_id=tx.id,
                counterparty_id=alloc.counterparty_id,
                contract_id=alloc.contract_id,
                invoice_id=alloc.invoice_id,
                allocated_amount=alloc.amount,
                state=MatchState.PENDING if pending else MatchState.AUTO,
                rule=alloc.rule,
            )
            self.session.add(match)
            matches.append(match)
            if alloc.invoice_id is not None:
                invoice = await self.session.get(Invoice, alloc.invoice_id)
                if invoice is not None:
                    apply_payment_to_invoice(invoice, alloc.amount)
        await self.session.flush()
        return matches

    async def _allocations_for_1c(self, matches: list[Match]) -> list[dict]:
        """Привязки для payload документа: GUID объектов 1С (onec_ref) +
        внутренние id и имена (см. Allocation1C в accounting/documents.py).

        GUID появляются после синхронизации справочников из 1С (onec/sync.py);
        до неё поля *_ref пустые, и расширение показывает бухгалтеру имя/номер.
        """
        allocations: list[dict] = []
        for m in matches:
            counterparty = (
                await self.session.get(Counterparty, m.counterparty_id)
                if m.counterparty_id
                else None
            )
            contract = (
                await self.session.get(Contract, m.contract_id) if m.contract_id else None
            )
            invoice = (
                await self.session.get(Invoice, m.invoice_id) if m.invoice_id else None
            )
            allocations.append(
                {
                    "amount": str(m.allocated_amount),
                    "counterparty_ref": counterparty.onec_ref if counterparty else "",
                    "counterparty_id": m.counterparty_id,
                    "counterparty_name": counterparty.name if counterparty else "",
                    "contract_ref": contract.onec_ref if contract else "",
                    "contract_id": m.contract_id,
                    "contract_number": contract.number if contract else "",
                    "kvvo": contract.kvvo if contract else "",  # шаг 9 регламента
                    "invoice_ref": invoice.onec_ref if invoice else "",
                    "invoice_id": m.invoice_id,
                    "invoice_number": invoice.number if invoice else "",
                }
            )
        return allocations

    # --- Курсы -------------------------------------------------------------

    async def _contract_currency(self, matches: list[Match]) -> str:
        for m in matches:
            if m.contract_id is not None:
                contract = await self.session.get(Contract, m.contract_id)
                if contract is not None:
                    return contract.currency
        # Контракт не определён (ручная привязка только контрагента) —
        # валюта привязки стейблкоина.
        return "USD"

    async def _ensure_rate_snapshot(
        self, tx: Transaction, matches: list[Match]
    ) -> RateSnapshot:
        existing = await self.session.scalar(
            select(RateSnapshot).where(
                RateSnapshot.transaction_id == tx.id, RateSnapshot.purpose == "finality"
            )
        )
        if existing is not None:
            return existing
        snapshot = await self.rates.snapshot(
            asset_symbol=tx.asset.symbol,
            contract_currency=await self._contract_currency(matches),
            as_of=tx.finalized_at or tx.block_time,
            purpose="finality",
            transaction_id=tx.id,
        )
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot

    # --- Учёт ----------------------------------------------------------------

    async def _process_receipt(
        self, tx: Transaction, rate: RateSnapshot, allocations: list[dict], key: str
    ) -> OnecDocument:
        existing_lot = await self.session.scalar(
            select(Lot.id).where(Lot.transaction_id == tx.id)
        )
        if existing_lot is None:
            self.session.add(
                Lot(
                    transaction_id=tx.id,
                    asset_id=tx.asset_id,
                    wallet_id=tx.wallet_id,
                    acquired_at=tx.block_time,
                    quantity=tx.amount,
                    remaining=tx.amount,
                    unit_cost_rub=rate.asset_to_rub,
                )
            )
        doc = OnecDocument(
            idempotency_key=key,
            doc_type=OnecDocType.RECEIPT,
            transaction_id=tx.id,
            payload=build_receipt(tx, rate, allocations),
        )
        self.session.add(doc)
        return doc

    async def _process_disposal(
        self, tx: Transaction, rate: RateSnapshot, allocations: list[dict], key: str
    ) -> OnecDocument | None:
        lots = (
            (
                await self.session.execute(
                    select(Lot)
                    .where(Lot.asset_id == tx.asset_id, Lot.remaining > 0)
                    .order_by(Lot.acquired_at, Lot.id)
                )
            )
            .scalars()
            .all()
        )
        views = [
            LotView(
                id=lot.id,
                acquired_at=lot.acquired_at,
                remaining=lot.remaining,
                unit_cost_rub=lot.unit_cost_rub,
            )
            for lot in lots
        ]
        try:
            result = self.cost_method.dispose(
                views, tx.amount, proceeds_rub=tx.amount * rate.asset_to_rub
            )
        except InsufficientBalanceError as exc:
            log.warning(
                "Выбытие %s отложено: %s (возможно, не завершён бэкфилл поступлений)",
                tx.tx_hash,
                exc,
            )
            return None

        by_id = {lot.id: lot for lot in lots}
        for view in views:
            by_id[view.id].remaining = view.remaining
        for part in result.parts:
            self.session.add(
                DisposalLine(
                    transaction_id=tx.id,
                    lot_id=part.lot_id,
                    quantity=part.quantity,
                    cost_rub=part.cost_rub,
                )
            )
        # Замыкание регламента (шаг 6): связанное ожидание sent → matched —
        # до сборки payload, чтобы документ нёс итоговый статус (шаг 9).
        await mark_matched(self.session, tx)
        doc = OnecDocument(
            idempotency_key=key,
            doc_type=OnecDocType.DISPOSAL,
            transaction_id=tx.id,
            payload=build_disposal(
                tx, rate, allocations, result,
                aml=await aml_summary(self.session, tx),
            ),
        )
        self.session.add(doc)
        return doc

    async def _process_fee(self, tx: Transaction) -> OnecDocument | None:
        if tx.fee_amount <= 0:
            return None
        key = idempotency_key(tx, OnecDocType.FEE, tx.network.code)
        exists = await self.session.scalar(
            select(OnecDocument.id).where(OnecDocument.idempotency_key == key)
        )
        if exists is not None:
            return None
        try:
            # purpose="fee": снимок комиссии не должен подменять снимок актива
            # при повторной обработке (_ensure_rate_snapshot ищет "finality").
            fee_rate = await self.rates.snapshot(
                asset_symbol=tx.fee_asset,
                contract_currency="USD",
                as_of=tx.finalized_at or tx.block_time,
                purpose="fee",
                transaction_id=tx.id,
            )
        except LookupError:
            # Нативная монета (TRX/ETH) не котируется настроенными источниками —
            # комиссия будет оценена, когда подключат биржевой источник.
            log.warning("Комиссия %s %s не оценена: нет источника котировки",
                        tx.fee_amount, tx.fee_asset)
            return None
        self.session.add(fee_rate)
        doc = OnecDocument(
            idempotency_key=key,
            doc_type=OnecDocType.FEE,
            transaction_id=tx.id,
            payload=build_fee(tx, fee_rub=tx.fee_amount * fee_rate.asset_to_rub),
        )
        self.session.add(doc)
        return doc
