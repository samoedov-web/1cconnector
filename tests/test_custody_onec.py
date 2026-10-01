"""Tests for custody reconciliation shadow exchange with 1C."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from connector.custody.onec import queue_reconciliation_info
from connector.models import (
    OnecDocType,
    OnecDocument,
    ReconciliationResult,
    ReconciliationRun,
)


def dt(day: int) -> datetime:
    return datetime(2026, 7, day, 12, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_queue_reconciliation_info_is_idempotent(async_session):
    run = ReconciliationRun(
        source_id="mock-depo",
        period_from=dt(1),
        period_to=dt(31),
        config={},
        config_hash="onec-shadow-test",
    )
    async_session.add(run)
    await async_session.flush()

    async_session.add_all(
        [
            ReconciliationResult(
                run_id=run.id,
                status="matched",
                rule="hash",
                ledger_tx_ids=[1],
                custody_entry_ids=[1],
                detail={},
            ),
            ReconciliationResult(
                run_id=run.id,
                status="missing_in_custody",
                rule="none",
                ledger_tx_ids=[2],
                custody_entry_ids=[],
                detail={"reason": "test"},
            ),
        ]
    )
    await async_session.flush()

    first = await queue_reconciliation_info(async_session, run)
    second = await queue_reconciliation_info(async_session, run)

    assert first is not None
    assert second is None

    assert first.doc_type == OnecDocType.RECONCILIATION
    assert first.transaction_id is None

    payload = first.payload

    assert payload["doc_type"] == "reconciliation_info"
    assert payload["run_id"] == run.id
    assert payload["source_id"] == "mock-depo"

    # В 1С shadow-регистр должны уходить только расхождения.
    assert len(payload["discrepancies"]) == 1
    assert payload["discrepancies"][0]["status"] == "missing_in_custody"

    docs = (
        await async_session.execute(
            select(OnecDocument).where(
                OnecDocument.idempotency_key
                == f"recon:mock-depo:{run.id}"
            )
        )
    ).scalars().all()

    assert len(docs) == 1
