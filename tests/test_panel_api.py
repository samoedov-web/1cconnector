"""Тесты API веб-панели: транзакции, справочник, дашборд, кошельки, очередь."""

from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from connector.config import settings
from connector.db import get_session
from connector.main import app
from connector.models import (
    Asset,
    Base,
    Contract,
    Counterparty,
    CounterpartyAddress,
    Direction,
    Invoice,
    Network,
    Role,
    Transaction,
    TxStatus,
    User,
    Wallet,
)
from connector.pipeline import TransactionPipeline
from connector.rates.service import RateService
from connector.security import create_token, hash_password

from tests.test_pipeline import FakeRateSource

SECRET = "test-secret"


def dt(day: int) -> datetime:
    return datetime(2026, 6, day, tzinfo=timezone.utc)


@pytest.fixture
async def env(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", SECRET)
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

    # Мир: сеть, кошелёк, контрагент с договором и инвойсом, две транзакции
    # (одна сматчена конвейером, вторая — в очереди разбора).
    async with factory() as session:
        operator = User(
            username="op", password_hash=hash_password("op-pass-1234"), role=Role.OPERATOR
        )
        network = Network(code="tron", name="TRON", finality_depth=19)
        session.add_all([operator, network])
        await session.flush()
        asset = Asset(network_id=network.id, symbol="USDT", contract_address="TUsdt", decimals=6)
        wallet = Wallet(network_id=network.id, address="TMyWallet", label="Основной")
        counterparty = Counterparty(name="Shanghai Trade Co.", onec_ref="cp-guid")
        session.add_all([asset, wallet, counterparty])
        await session.flush()
        session.add(
            CounterpartyAddress(
                counterparty_id=counterparty.id, network_id=network.id, address="TPartner"
            )
        )
        contract = Contract(
            counterparty_id=counterparty.id, number="ВЭД-1", currency="USD", onec_ref="c-guid"
        )
        session.add(contract)
        await session.flush()
        invoice = Invoice(
            contract_id=contract.id,
            number="INV-1",
            amount=Decimal(1000),
            currency="USD",
            due_from=dt(1),
            due_to=dt(28),
        )
        session.add(invoice)

        def tx(tx_hash: str, from_addr: str, day: int) -> Transaction:
            return Transaction(
                network_id=network.id,
                asset_id=asset.id,
                wallet_id=wallet.id,
                tx_hash=tx_hash,
                log_index=0,
                block_number=1000 + day,
                block_time=dt(day),
                direction=Direction.IN,
                from_address=from_addr,
                to_address="TMyWallet",
                amount=Decimal(1000),
                status=TxStatus.FINAL,
                confirmations=25,
                finalized_at=dt(day),
                raw_response={},
                source="test",
            )

        session.add_all([tx("matched", "TPartner", 5), tx("unmatched", "TStranger", 10)])
        await session.flush()
        pipeline = TransactionPipeline(session, RateService(primary=FakeRateSource()))
        await pipeline.process_network(network)
        await session.commit()
        ids = {
            "operator_id": operator.id,
            "counterparty_id": counterparty.id,
            "contract_id": contract.id,
            "invoice_id": invoice.id,
            "wallet_id": wallet.id,
        }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={
            "Authorization": "Bearer "
            + create_token(ids["operator_id"], Role.OPERATOR, SECRET, 3600)
        },
    ) as client:
        yield {"client": client, **ids}

    app.dependency_overrides.clear()
    await engine.dispose()


async def test_transactions_list_and_filters(env):
    client = env["client"]
    r = await client.get("/api/v1/transactions")
    assert r.status_code == 200
    assert r.json()["total"] == 2

    r = await client.get("/api/v1/transactions", params={"tx_hash": "match"})
    assert [t["tx_hash"] for t in r.json()["items"]] == ["matched"]

    r = await client.get("/api/v1/transactions", params={"network": "ethereum"})
    assert r.json()["total"] == 0


async def test_transaction_card_shows_allocations_and_documents(env):
    client = env["client"]
    r = await client.get("/api/v1/transactions", params={"tx_hash": "matched"})
    tx_id = r.json()["items"][0]["id"]

    r = await client.get(f"/api/v1/transactions/{tx_id}")
    card = r.json()
    assert card["allocations"][0]["counterparty"] == "Shanghai Trade Co."
    assert card["immutability"]["tx_hash"] == "matched"
    assert card["matches"][0]["state"] == "auto"
    assert card["onec_documents"][0]["doc_type"] == "receipt"

    assert (await client.get("/api/v1/transactions/9999")).status_code == 404


async def test_pending_queue_enriched_and_resolvable(env):
    client = env["client"]
    r = await client.get("/api/v1/matching/pending")
    [row] = r.json()
    assert row["tx_hash"] == "unmatched"
    assert row["counterparty_address"] == "TStranger"
    assert row["asset"] == "USDT"

    r = await client.post(
        f"/api/v1/matching/{row['match_id']}/resolve",
        json={
            "counterparty_id": env["counterparty_id"],
            "contract_id": env["contract_id"],
            "remember_address": True,
        },
    )
    assert r.status_code == 200
    assert (await client.get("/api/v1/matching/pending")).json() == []


async def test_directory_list_detail_and_manual_creation(env):
    client = env["client"]
    r = await client.get("/api/v1/counterparties")
    [row] = r.json()
    assert row["name"] == "Shanghai Trade Co."
    assert row["contracts"] == 1

    r = await client.get(f"/api/v1/counterparties/{row['id']}")
    detail = r.json()
    assert detail["contracts"][0]["number"] == "ВЭД-1"
    assert detail["contracts"][0]["invoices"][0]["number"] == "INV-1"
    assert detail["addresses"][0]["address"] == "TPartner"

    r = await client.post("/api/v1/counterparties", json={"name": "New Partner LLC"})
    assert r.status_code == 201
    new_id = r.json()["id"]
    r = await client.post(
        f"/api/v1/counterparties/{new_id}/addresses",
        json={"network_code": "tron", "address": "TNewAddr"},
    )
    assert r.status_code == 201
    # Дубль адреса в той же сети — конфликт.
    r = await client.post(
        f"/api/v1/counterparties/{new_id}/addresses",
        json={"network_code": "tron", "address": "TNewAddr"},
    )
    assert r.status_code == 409


async def test_wallets_and_dashboard(env):
    client = env["client"]
    r = await client.get("/api/v1/wallets")
    [w] = r.json()
    assert w["address"] == "TMyWallet"

    r = await client.get("/api/v1/dashboard")
    data = r.json()
    assert data["transactions"]["final"] == 2
    assert data["pending_matches"] == 1
    assert data["onec_documents"]["draft"] == 1
    assert data["networks"][0]["code"] == "tron"
    assert data["networks"][0]["wallets"] == 1
