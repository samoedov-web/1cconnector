"""Фаза 5 depository-спеки: вкладка «Сверка», отчёт, регистр для 1С (shadow)."""

from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from connector.config import settings
from connector.custody.fixtures import DistortionOptions, distort, write_fixture
from connector.custody.store import EntryPayload
from connector.db import get_session
from connector.main import app
from connector.models import (
    Asset,
    Base,
    CustodyOperationType,
    Direction,
    Network,
    OnecDocType,
    OnecDocument,
    Role,
    Transaction,
    TxStatus,
    User,
    Wallet,
)
from connector.security import create_token, hash_password

SECRET = "test-secret"


def dt(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 7, day, hour, tzinfo=timezone.utc)


@pytest.fixture
async def env(monkeypatch, tmp_path):
    # Фикстура выписки: один матч по хэшу + одна лишняя запись.
    entries = [
        EntryPayload(
            entry_id="1", occurred_at=dt(6), asset="USDT",
            amount=Decimal("48500"), operation_type=CustodyOperationType.DEPOSIT,
            network="tron", external_tx_hash="chainhash",
        ),
    ]
    entries = entries + distort([], DistortionOptions(extra=1), seed=7)
    write_fixture(tmp_path / "july.json", "ST-JULY", dt(1), dt(31), entries)

    monkeypatch.setattr(settings, "secret_key", SECRET)
    monkeypatch.setattr(settings, "custody_fixtures_dir", str(tmp_path))
    monkeypatch.setattr(settings, "custody_mode", "shadow")

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    async with factory() as session:
        operator = User(username="op", password_hash=hash_password("x" * 8),
                        role=Role.OPERATOR)
        auditor = User(username="aud", password_hash=hash_password("x" * 8),
                       role=Role.AUDITOR)
        network = Network(code="tron", name="TRON", finality_depth=19)
        session.add_all([operator, auditor, network])
        await session.flush()
        asset = Asset(network_id=network.id, symbol="USDT",
                      contract_address="T", decimals=6)
        wallet = Wallet(network_id=network.id, address="TMyWallet")
        session.add_all([asset, wallet])
        await session.flush()
        session.add(Transaction(
            network_id=network.id, asset_id=asset.id, wallet_id=wallet.id,
            tx_hash="chainhash", log_index=0, block_number=100, block_time=dt(6),
            direction=Direction.IN, from_address="TP", to_address="TMyWallet",
            amount=Decimal("48500"), status=TxStatus.FINAL, confirmations=30,
            finalized_at=dt(6), raw_response={}, source="test",
        ))
        await session.commit()
        ids = {"operator_id": operator.id, "auditor_id": auditor.id}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "factory": factory, **ids}

    app.dependency_overrides.clear()
    await engine.dispose()


def auth(user_id: int, role: Role) -> dict:
    return {"Authorization": "Bearer " + create_token(user_id, role, SECRET, 3600)}


PERIOD = {"period_from": dt(1).isoformat(), "period_to": dt(31).isoformat()}


async def test_full_flow_fetch_run_report_resolve(env):
    client = env["client"]
    op = auth(env["operator_id"], Role.OPERATOR)

    # Источник и его capabilities видны.
    r = await client.get("/api/v1/reconciliation/sources", headers=op)
    assert r.status_code == 200
    assert r.json()["active"] == "mock-depo"
    assert r.json()["capabilities"]["has_tx_hash"] is True

    # 1. Загрузка выписок в журнал неизменяемости.
    r = await client.post("/api/v1/reconciliation/fetch", json=PERIOD, headers=op)
    assert r.status_code == 200
    assert r.json() == {"statements": 1}
    # Повторная загрузка идемпотентна.
    r = await client.post("/api/v1/reconciliation/fetch", json=PERIOD, headers=op)
    assert r.status_code == 200

    # 2. Запуск сверки: матч по хэшу + лишняя запись выписки.
    r = await client.post("/api/v1/reconciliation/runs", json=PERIOD, headers=op)
    assert r.status_code == 200
    run = r.json()
    assert run["counts"]["matched"] == 1
    assert run["counts"]["missing_on_chain"] == 1
    assert run["discrepancies"] == 1

    # Список и детализация.
    r = await client.get("/api/v1/reconciliation/runs", headers=op)
    assert [item["id"] for item in r.json()] == [run["id"]]
    r = await client.get(f"/api/v1/reconciliation/runs/{run['id']}", headers=op)
    detail = r.json()
    assert detail["discrepancies"] == 1
    discrepancy = next(
        row for row in detail["rows"] if row["status"] == "missing_on_chain"
    )

    # 3. Отчёт: HTML/XLSX/JSON.
    for fmt in ("html", "xlsx", "json"):
        r = await client.get(
            f"/api/v1/reports/custody-reconciliation/{run['id']}",
            params={"format": fmt}, headers=op,
        )
        assert r.status_code == 200, fmt
    assert "АКТ СВЕРКИ" in (await client.get(
        f"/api/v1/reports/custody-reconciliation/{run['id']}",
        params={"format": "html"}, headers=op,
    )).text

    # 4. Ручной разбор расхождения.
    r = await client.post(
        f"/api/v1/reconciliation/results/{discrepancy['result_id']}/resolve",
        json={"note": "перевод учтён вручную"}, headers=op,
    )
    assert r.status_code == 200
    r = await client.get(f"/api/v1/reconciliation/runs/{run['id']}", headers=op)
    statuses = {row["status"] for row in r.json()["rows"]}
    assert "missing_on_chain" not in statuses
    assert "manual" in statuses
    # Разобранную строку нельзя разобрать повторно.
    r = await client.post(
        f"/api/v1/reconciliation/results/{discrepancy['result_id']}/resolve",
        json={}, headers=op,
    )
    assert r.status_code == 400


async def test_shadow_register_queued_with_discrepancies_only(env):
    client = env["client"]
    op = auth(env["operator_id"], Role.OPERATOR)
    await client.post("/api/v1/reconciliation/fetch", json=PERIOD, headers=op)
    r = await client.post("/api/v1/reconciliation/runs", json=PERIOD, headers=op)
    run_id = r.json()["id"]
    # Повторный запуск не создаёт второй записи (идемпотентность).
    await client.post("/api/v1/reconciliation/runs", json=PERIOD, headers=op)

    async with env["factory"]() as session:
        docs = (
            (await session.execute(select(OnecDocument).where(
                OnecDocument.doc_type == OnecDocType.RECONCILIATION)))
            .scalars().all()
        )
    assert len(docs) == 1
    payload = docs[0].payload
    assert payload["run_id"] == run_id
    # В регистр уходят только расхождения — matched строк нет.
    assert {row["status"] for row in payload["discrepancies"]} == {"missing_on_chain"}
    assert payload["summary"]


async def test_rbac_auditor_reads_but_cannot_run(env):
    client = env["client"]
    aud = auth(env["auditor_id"], Role.AUDITOR)
    assert (await client.get("/api/v1/reconciliation/runs", headers=aud)).status_code == 200
    assert (
        await client.post("/api/v1/reconciliation/fetch", json=PERIOD, headers=aud)
    ).status_code == 403
    assert (
        await client.post("/api/v1/reconciliation/runs", json=PERIOD, headers=aud)
    ).status_code == 403
