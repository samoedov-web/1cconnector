"""Deterministic outgoing matching by expected tx_hash."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from connector.aml.flow import link_outgoing_payments
from connector.models import (
    Alert,
    ExpectedPayment,
    ExpectedPaymentStatus as EPS,
)


@pytest.mark.asyncio
async def test_exact_expected_tx_hash_has_priority(
    async_session,
    sample_invoice,
    sample_transaction,
):
    sample_transaction.direction = "out"
    sample_transaction.tx_hash = "0xdeterministic"
    sample_transaction.to_address = "0xDEST"
    sample_transaction.amount = Decimal("100")

    payment = ExpectedPayment(
        invoice_id=sample_invoice.id,
        to_address="0xDEST",
        network="testnet",
        amount=Decimal("100"),
        currency="USD",
        tolerance=Decimal("0.005"),
        status=EPS.AML_APPROVED,
        expected_tx_hash="0xdeterministic",
    )

    async_session.add(payment)
    await async_session.flush()

    linked = await link_outgoing_payments(
        async_session,
        [sample_transaction],
        "testnet",
    )

    assert [p.id for p in linked] == [payment.id]
    assert payment.transaction_id == sample_transaction.id
    assert payment.status == EPS.SENT


@pytest.mark.asyncio
async def test_hash_amount_conflict_does_not_fallback(
    async_session,
    sample_invoice,
    sample_transaction,
):
    sample_transaction.direction = "out"
    sample_transaction.tx_hash = "0xconflict"
    sample_transaction.to_address = "0xDEST"
    sample_transaction.amount = Decimal("150")

    payment = ExpectedPayment(
        invoice_id=sample_invoice.id,
        to_address="0xDEST",
        network="testnet",
        amount=Decimal("100"),
        currency="USD",
        tolerance=Decimal("0.005"),
        status=EPS.AML_APPROVED,
        expected_tx_hash="0xconflict",
    )

    async_session.add(payment)
    await async_session.flush()

    linked = await link_outgoing_payments(
        async_session,
        [sample_transaction],
        "testnet",
    )

    assert linked == []
    assert payment.transaction_id is None
    assert payment.status == EPS.AML_APPROVED

    alert = await async_session.scalar(
        select(Alert).where(
            Alert.title.contains("tx_hash")
        )
    )

    assert alert is not None


@pytest.mark.asyncio
async def test_without_hash_keeps_address_amount_fallback(
    async_session,
    sample_invoice,
    sample_transaction,
):
    sample_transaction.direction = "out"
    sample_transaction.tx_hash = "0xfallback"
    sample_transaction.to_address = "0xDEST"
    sample_transaction.amount = Decimal("100")

    payment = ExpectedPayment(
        invoice_id=sample_invoice.id,
        to_address="0xDEST",
        network="testnet",
        amount=Decimal("100"),
        currency="USD",
        tolerance=Decimal("0.005"),
        status=EPS.AML_APPROVED,
        expected_tx_hash=None,
    )

    async_session.add(payment)
    await async_session.flush()

    linked = await link_outgoing_payments(
        async_session,
        [sample_transaction],
        "testnet",
    )

    assert [p.id for p in linked] == [payment.id]
    assert payment.transaction_id == sample_transaction.id
