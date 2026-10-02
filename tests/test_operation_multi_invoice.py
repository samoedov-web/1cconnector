from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from connector.models import Invoice, OperationInvoice


def dt(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_operation_can_include_multiple_invoices(
    async_session,
    sample_operation,
    sample_invoice,
):
    second = Invoice(
        contract_id=sample_invoice.contract_id,
        number="INV-MULTI-2",
        amount=Decimal("250"),
        currency=sample_invoice.currency,
        due_from=dt(1),
        due_to=dt(31),
    )
    async_session.add(second)
    await async_session.flush()

    async_session.add_all(
        [
            OperationInvoice(
                operation_id=sample_operation.id,
                invoice_id=sample_invoice.id,
            ),
            OperationInvoice(
                operation_id=sample_operation.id,
                invoice_id=second.id,
            ),
        ]
    )
    await async_session.flush()

    rows = (
        await async_session.execute(
            select(OperationInvoice)
            .where(OperationInvoice.operation_id == sample_operation.id)
            .order_by(OperationInvoice.invoice_id)
        )
    ).scalars().all()

    assert {row.invoice_id for row in rows} == {
        sample_invoice.id,
        second.id,
    }

    # Legacy 1:1 field remains untouched until separate migration/backfill.
    assert sample_operation.invoice_id == sample_invoice.id


@pytest.mark.asyncio
async def test_operation_invoice_membership_is_unique(
    async_session,
    sample_operation,
    sample_invoice,
):
    async_session.add(
        OperationInvoice(
            operation_id=sample_operation.id,
            invoice_id=sample_invoice.id,
        )
    )
    await async_session.flush()

    async_session.add(
        OperationInvoice(
            operation_id=sample_operation.id,
            invoice_id=sample_invoice.id,
        )
    )

    with pytest.raises(IntegrityError):
        await async_session.flush()

    await async_session.rollback()
