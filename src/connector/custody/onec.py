"""Информационная запись сверки для 1С — режим shadow (п. 4.6 спеки).

В shadow-режиме 1С получает ТОЛЬКО запись в регистр сведений «Результаты
сверки с депозитарием»: сводка + расхождения. Никаких бухгалтерских
документов — источник истины для учёта остаётся блокчейн.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from connector.custody.report import (
    DISCREPANCY_STATUSES,
    reconciliation_report_data,
)
from connector.models import OnecDocType, OnecDocument, ReconciliationRun


def reconciliation_doc_key(run: ReconciliationRun) -> str:
    return f"recon:{run.source_id}:{run.id}"


async def queue_reconciliation_info(
    session: AsyncSession, run: ReconciliationRun
) -> OnecDocument | None:
    """Поставить запись сверки в очередь обмена; идемпотентно по запуску."""
    key = reconciliation_doc_key(run)
    exists = await session.scalar(
        select(OnecDocument.id).where(OnecDocument.idempotency_key == key)
    )
    if exists is not None:
        return None
    data = await reconciliation_report_data(session, run.id)
    payload = {
        "doc_type": OnecDocType.RECONCILIATION.value,
        "run_id": run.id,
        "source_id": run.source_id,
        "period_from": run.period_from.isoformat(),
        "period_to": run.period_to.isoformat(),
        "stale": run.stale,
        "summary": data["summary"],
        # В регистр уходят только расхождения — matched-строки информационно
        # не нужны и раздували бы обмен.
        "discrepancies": [
            row for row in data["rows"] if row["status"] in DISCREPANCY_STATUSES
        ],
    }
    doc = OnecDocument(
        idempotency_key=key,
        doc_type=OnecDocType.RECONCILIATION,
        payload=payload,
    )
    session.add(doc)
    await session.flush()
    return doc
