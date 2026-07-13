"""Фаза 3 aml-спеки: роли compliance/treasurer, очередь решений, аудит."""

import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from connector.aml.mock_adapter import MockAmlAdapter
from connector.config import settings
from connector.db import get_session
from connector.main import app
from connector.models import (
    AuditLog,
    Base,
    ExpectedPayment,
    ExpectedPaymentStatus as EPS,
    Network,
    Role,
    User,
)
from connector.onec.sync import ContractIn, CounterpartyIn, InvoiceIn, SyncIn, apply_sync
from connector.security import create_token, hash_password

SECRET = "test-secret"
GREY = "TGreyAddress222222222222222222222"
CLEAN = "TCleanAddress11111111111111111111"


def dt(day: int) -> datetime:
    return datetime(2026, 7, day, 12, tzinfo=timezone.utc)


@pytest.fixture
async def env(monkeypatch, tmp_path):
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

    fixtures = tmp_path / "aml.json"
    fixtures.write_text(json.dumps({
        "default_score": 5,
        "addresses": {GREY: {"risk_score": 50, "categories": ["mixer"]}},
    }), encoding="utf-8")
    adapter = MockAmlAdapter(str(fixtures))

    users = {}
    async with factory() as session:
        for username, role in [
            ("comp", Role.COMPLIANCE), ("trez", Role.TREASURER),
            ("aud", Role.AUDITOR), ("boss", Role.ADMIN),
        ]:
            user = User(username=username,
                        password_hash=hash_password("x" * 8), role=role)
            session.add(user)
            await session.flush()
            users[username] = user.id
        session.add(Network(code="tron", name="TRON", finality_depth=19))
        await session.flush()
        # Два ожидаемых платежа: серый (review) и чистый (approved).
        for ref, number, address in [("i-1", "INV-1", GREY), ("i-2", "INV-2", CLEAN)]:
            await apply_sync(session, SyncIn(
                counterparties=[CounterpartyIn(onec_ref="cp-1", name="Shanghai Trade")],
                contracts=[ContractIn(onec_ref="c-1", counterparty_ref="cp-1",
                                      number="ВЭД-1", currency="USD")],
                invoices=[InvoiceIn(onec_ref=ref, contract_ref="c-1", number=number,
                                    amount=Decimal(50000), currency="USD",
                                    due_from=dt(1), due_to=dt(28),
                                    crypto_address=address)],
            ), aml_adapter=adapter)
        await session.commit()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "factory": factory, "users": users}

    app.dependency_overrides.clear()
    await engine.dispose()


def auth(env, username: str, role: Role) -> dict:
    return {"Authorization": "Bearer "
            + create_token(env["users"][username], role, SECRET, 3600)}


async def test_treasurer_sees_approved_queue(env):
    client = env["client"]
    r = await client.get("/api/v1/aml/payments",
                         params={"status": "aml_approved"},
                         headers=auth(env, "trez", Role.TREASURER))
    assert r.status_code == 200
    [row] = r.json()
    assert row["invoice_number"] == "INV-2"
    assert row["risk_score"] == 5
    assert row["counterparty"] == "Shanghai Trade"


async def test_compliance_approves_grey_payment_with_audit(env):
    client = env["client"]
    comp = auth(env, "comp", Role.COMPLIANCE)
    r = await client.get("/api/v1/aml/payments",
                         params={"status": "aml_review"}, headers=comp)
    [row] = r.json()
    assert row["categories"] == ["mixer"]

    r = await client.post(f"/api/v1/aml/payments/{row['id']}/decide",
                          json={"action": "approve", "note": "контрагент проверен"},
                          headers=comp)
    assert r.status_code == 200
    assert r.json()["status"] == "aml_approved"

    async with env["factory"]() as session:
        payment = await session.get(ExpectedPayment, row["id"])
        assert payment.status == EPS.AML_APPROVED
        assert payment.decided_by == "comp"
        assert payment.decision_note == "контрагент проверен"
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "aml_decision")
        )
        assert audit.actor == "comp"
        assert audit.details["action"] == "approve"

    # Повторное решение по уже решённому — 400 (статусная машина).
    r = await client.post(f"/api/v1/aml/payments/{row['id']}/decide",
                          json={"action": "reject"}, headers=comp)
    assert r.status_code == 400
    assert "не ожидает решения" in r.json()["detail"]


async def test_compliance_can_reject(env):
    client = env["client"]
    comp = auth(env, "comp", Role.COMPLIANCE)
    r = await client.get("/api/v1/aml/payments",
                         params={"status": "aml_review"}, headers=comp)
    [row] = r.json()
    r = await client.post(f"/api/v1/aml/payments/{row['id']}/decide",
                          json={"action": "reject", "note": "миксер в цепочке"},
                          headers=comp)
    assert r.json()["status"] == "aml_rejected"


async def test_rbac_only_compliance_or_admin_decides(env):
    client = env["client"]
    r = await client.get("/api/v1/aml/payments",
                         headers=auth(env, "comp", Role.COMPLIANCE))
    review = next(p for p in r.json() if p["status"] == "aml_review")

    for username, role in [("trez", Role.TREASURER), ("aud", Role.AUDITOR)]:
        r = await client.post(f"/api/v1/aml/payments/{review['id']}/decide",
                              json={"action": "approve"},
                              headers=auth(env, username, role))
        assert r.status_code == 403, username

    # Админ — может; аудитор и казначей — читают списки.
    r = await client.post(f"/api/v1/aml/payments/{review['id']}/decide",
                          json={"action": "approve"},
                          headers=auth(env, "boss", Role.ADMIN))
    assert r.status_code == 200
    for username, role in [("trez", Role.TREASURER), ("aud", Role.AUDITOR)]:
        assert (await client.get(
            "/api/v1/aml/payments", headers=auth(env, username, role)
        )).status_code == 200


async def test_new_roles_can_login_and_read_dashboard(env):
    client = env["client"]
    r = await client.post("/api/v1/auth/login",
                          json={"username": "comp", "password": "x" * 8})
    assert r.status_code == 200
    assert r.json()["role"] == "compliance"
    token = r.json()["token"]
    r = await client.get("/api/v1/dashboard",
                         headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200