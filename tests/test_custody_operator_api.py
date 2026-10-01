"""Canonical operator API tests for depository reconciliation."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import connector.api.custody_routes as custody_api
from connector.config import settings
from connector.custody.base import Capabilities, EntryGranularity
from connector.custody.store import EntryPayload, StatementPayload
from connector.db import get_session
from connector.main import app
from connector.models import (
    AuditLog,
    Base,
    CustodyOperationType,
    CustodyStatement,
    ReconciliationResult,
    ReconciliationRun,
    Role,
)
from connector.security import (
    CurrentUser,
    require_operator,
    require_reader,
)
from connector.sources.base import HealthStatus


def dt(day: int) -> datetime:
    return datetime(2026, 7, day, 12, tzinfo=timezone.utc)


@pytest.fixture
async def api_env(monkeypatch):
    monkeypatch.setattr(settings, "custody_mode", "shadow")
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with factory() as session:
            yield session

    reader = CurrentUser(
        id=1,
        username="reader",
        role=Role.AUDITOR,
    )
    operator = CurrentUser(
        id=2,
        username="operator",
        role=Role.OPERATOR,
    )

    async def override_reader():
        return reader

    async def override_operator():
        return operator

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[require_reader] = override_reader
    app.dependency_overrides[require_operator] = override_operator

    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        yield client, factory

    app.dependency_overrides.clear()
    await engine.dispose()


class FakeAdapter:
    def capabilities(self):
        return Capabilities(
            has_tx_hash=True,
            has_counterparty=True,
            has_network=True,
            entry_granularity=EntryGranularity.PER_TX,
        )

    async def health(self):
        return HealthStatus(status="ok")

    async def fetch_statements(self, period_from, period_to):
        return [
            StatementPayload(
                statement_id="stmt-api-1",
                source_id="mock-depo",
                period_from=period_from,
                period_to=period_to,
                issued_at=period_to,
                raw_payload={
                    "statement_id": "stmt-api-1",
                    "granularity": "per_tx",
                },
            )
        ]

    async def fetch_entries(self, statement_id):
        assert statement_id == "stmt-api-1"
        return [
            EntryPayload(
                entry_id="entry-api-1",
                occurred_at=dt(10),
                asset="USDT",
                amount=100,
                operation_type=CustodyOperationType.DEPOSIT,
                network="ethereum",
                external_tx_hash="0x-api-test",
                raw_line={"id": "entry-api-1"},
            )
        ]


@pytest.mark.asyncio
async def test_sources_exposes_active_adapter(api_env, monkeypatch):
    client, _ = api_env

    monkeypatch.setattr(
        custody_api,
        "_adapter",
        lambda: FakeAdapter(),
    )

    response = await client.get(
        "/api/v1/custody/reconciliation/sources"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["active"] == "mock-depo"
    assert "mock-depo" in data["registered"]
    assert data["health"]["status"] == "ok"
    assert data["capabilities"]["has_tx_hash"] is True
    assert data["capabilities"]["entry_granularity"] == "per_tx"


@pytest.mark.asyncio
async def test_fetch_stores_statement_and_audit(
    api_env,
    monkeypatch,
):
    client, factory = api_env

    monkeypatch.setattr(
        custody_api,
        "_adapter",
        lambda: FakeAdapter(),
    )

    response = await client.post(
        "/api/v1/custody/reconciliation/fetch",
        json={
            "period_from": dt(1).isoformat(),
            "period_to": dt(31).isoformat(),
        },
    )

    assert response.status_code == 200
    assert response.json() == {"statements": 1}

    async with factory() as session:
        statement = await session.scalar(
            select(CustodyStatement).where(
                CustodyStatement.statement_id == "stmt-api-1"
            )
        )

        audit = await session.scalar(
            select(AuditLog).where(
                AuditLog.action == "custody_statements_fetched"
            )
        )

        assert statement is not None
        assert statement.source_id == "mock-depo"

        assert audit is not None
        assert audit.actor == "operator"
        assert audit.details["statements"] == 1


@pytest.mark.asyncio
async def test_run_list_detail_and_results(api_env):
    client, _ = api_env

    response = await client.post(
        "/api/v1/custody/reconciliation/runs",
        json={
            "source_id": "mock-depo",
            "period_from": dt(1).isoformat(),
            "period_to": dt(31).isoformat(),
            "tolerance_abs": "0.01",
            "tolerance_rel": "0",
            "date_window_hours": 24,
            "accept_aggregates": True,
        },
    )

    assert response.status_code == 200

    created = response.json()

    assert created["id"] > 0
    assert created["mode"] == "shadow"
    assert created["status"] == "completed"

    run_id = created["id"]

    response = await client.get(
        "/api/v1/custody/reconciliation/runs"
    )

    assert response.status_code == 200
    assert any(row["id"] == run_id for row in response.json())

    response = await client.get(
        f"/api/v1/custody/reconciliation/runs/{run_id}"
    )

    assert response.status_code == 200
    assert response.json()["run"]["id"] == run_id

    response = await client.get(
        f"/api/v1/custody/reconciliation/runs/{run_id}/results"
    )

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_manual_resolution_updates_result_and_audit(api_env):
    client, factory = api_env

    async with factory() as session:
        run = ReconciliationRun(
            source_id="mock-depo",
            period_from=dt(1),
            period_to=dt(31),
            config={},
            config_hash="manual-test",
        )
        session.add(run)
        await session.flush()

        result = ReconciliationResult(
            run_id=run.id,
            status="missing_in_custody",
            rule="none",
            ledger_tx_ids=[101],
            custody_entry_ids=[],
            detail={},
        )
        session.add(result)
        await session.commit()

        result_id = result.id

    response = await client.post(
        f"/api/v1/custody/reconciliation/results/{result_id}/resolve",
        json={
            "note": "Проверено оператором",
            "ledger_tx_ids": [102],
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "manual"}

    async with factory() as session:
        result = await session.get(
            ReconciliationResult,
            result_id,
        )

        audit = await session.scalar(
            select(AuditLog).where(
                AuditLog.action == "reconciliation_resolved"
            )
        )

        assert result.status == "manual"
        assert result.rule == "manual"
        assert result.ledger_tx_ids == [101, 102]
        assert result.detail["previous_status"] == "missing_in_custody"
        assert result.detail["resolved_by"] == "operator"
        assert result.detail["note"] == "Проверено оператором"

        assert audit is not None
        assert audit.actor == "operator"
        assert audit.entity_id == str(result_id)
