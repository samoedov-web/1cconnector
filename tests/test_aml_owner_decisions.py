"""Решения владельца 2026-07-14: кнопка «Отправил» + срок одобрения 48 ч.

Сценарий 4 регламента (таймер обнаружения) и вопрос 7.1 aml-спеки.
"""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from connector.aml.flow import (
    check_sent_timeouts,
    expire_stale_approvals,
    transition,
)
from connector.aml.mock_adapter import MockAmlAdapter
from connector.config import settings
from connector.db import get_session
from connector.main import app
from connector.models import (
    Alert,
    AmlScreening,
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
CLEAN = "TCleanAddress11111111111111111111"
NOW = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)


def dt(day: int) -> datetime:
    return datetime(2026, 7, day, 12, tzinfo=timezone.utc)


def batch(address: str = CLEAN) -> SyncIn:
    return SyncIn(
        counterparties=[CounterpartyIn(onec_ref="cp-1", name="Shanghai Trade")],
        contracts=[ContractIn(onec_ref="c-1", counterparty_ref="cp-1",
                              number="ВЭД-1", currency="USD")],
        invoices=[InvoiceIn(onec_ref="i-1", contract_ref="c-1", number="INV-1",
                            amount=Decimal(50000), currency="USD",
                            due_from=dt(1), due_to=dt(28),
                            crypto_address=address)],
    )


def clean_adapter(tmp_path) -> MockAmlAdapter:
    path = tmp_path / "aml.json"
    path.write_text(json.dumps({"default_score": 5, "addresses": {}}),
                    encoding="utf-8")
    return MockAmlAdapter(str(path))


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

    users = {}
    async with factory() as session:
        for username, role in [
            ("trez", Role.TREASURER), ("comp", Role.COMPLIANCE),
            ("aud", Role.AUDITOR), ("boss", Role.ADMIN),
        ]:
            user = User(username=username,
                        password_hash=hash_password("x" * 8), role=role)
            session.add(user)
            await session.flush()
            users[username] = user.id
        session.add(Network(code="tron", name="TRON", finality_depth=19))
        await session.flush()
        await apply_sync(session, batch(), aml_adapter=clean_adapter(tmp_path))
        await session.commit()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "factory": factory, "users": users}

    app.dependency_overrides.clear()
    await engine.dispose()


def auth(env, username: str, role: Role) -> dict:
    return {"Authorization": "Bearer "
            + create_token(env["users"][username], role, SECRET, 3600)}


async def payment_id(env) -> int:
    async with env["factory"]() as session:
        return await session.scalar(select(ExpectedPayment.id))


# --- Кнопка «Отправил» (API) ----------------------------------------------------


async def test_treasurer_marks_sent_with_audit(env):
    pid = await payment_id(env)
    r = await env["client"].post(f"/api/v1/aml/payments/{pid}/mark-sent",
                                 headers=auth(env, "trez", Role.TREASURER))
    assert r.status_code == 200
    assert r.json()["sent_marked_at"]

    async with env["factory"]() as session:
        payment = await session.get(ExpectedPayment, pid)
        assert payment.status == EPS.AML_APPROVED  # статус меняет только индексер
        assert payment.sent_marked_by == "trez"
        assert payment.sent_marked_at is not None
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "aml_marked_sent")
        )
        assert audit.actor == "trez"

    # Отметка в списке видна, повторная отметка — 400.
    rows = (await env["client"].get(
        "/api/v1/aml/payments", headers=auth(env, "trez", Role.TREASURER)
    )).json()
    assert rows[0]["sent_marked_by"] == "trez"
    r = await env["client"].post(f"/api/v1/aml/payments/{pid}/mark-sent",
                                 headers=auth(env, "trez", Role.TREASURER))
    assert r.status_code == 400
    assert "уже отмечена" in r.json()["detail"]


async def test_mark_sent_rbac_and_status_guard(env):
    pid = await payment_id(env)
    # Комплаенс и аудитор отмечать не могут — это действие казначея.
    for username, role in [("comp", Role.COMPLIANCE), ("aud", Role.AUDITOR)]:
        r = await env["client"].post(f"/api/v1/aml/payments/{pid}/mark-sent",
                                     headers=auth(env, username, role))
        assert r.status_code == 403, username

    # Неодобренный платёж отметить нельзя.
    async with env["factory"]() as session:
        payment = await session.get(ExpectedPayment, pid)
        transition(payment, EPS.EXPIRED)
        await session.commit()
    r = await env["client"].post(f"/api/v1/aml/payments/{pid}/mark-sent",
                                 headers=auth(env, "boss", Role.ADMIN))
    assert r.status_code == 400
    assert "только по одобренному" in r.json()["detail"]


# --- Таймер «не обнаружена за 30 минут» ------------------------------------------


async def test_sent_timeout_alerts_once(env):
    pid = await payment_id(env)
    async with env["factory"]() as session:
        payment = await session.get(ExpectedPayment, pid)
        payment.sent_marked_at = NOW - timedelta(minutes=31)
        payment.sent_marked_by = "trez"
        await session.commit()

    async with env["factory"]() as session:
        overdue = await check_sent_timeouts(session, now=NOW)
        assert [p.id for p in overdue] == [pid]
        alert = await session.scalar(select(Alert))
        assert "не обнаружена" in alert.title
        assert alert.details["marked_by"] == "trez"
        # Повторный цикл — алерт не дублируется.
        assert await check_sent_timeouts(session, now=NOW) == []
        assert await session.scalar(select(func.count(Alert.id))) == 1
        await session.commit()


async def test_sent_timeout_quiet_before_deadline_and_after_link(env):
    pid = await payment_id(env)
    async with env["factory"]() as session:
        payment = await session.get(ExpectedPayment, pid)
        payment.sent_marked_at = NOW - timedelta(minutes=29)
        await session.commit()
    async with env["factory"]() as session:
        assert await check_sent_timeouts(session, now=NOW) == []  # рано

        # Индексер обнаружил транзакцию (sent) — таймер закрыт сам собой.
        payment = await session.get(ExpectedPayment, pid)
        payment.sent_marked_at = NOW - timedelta(minutes=90)
        transition(payment, EPS.SENT)
        await session.commit()
    async with env["factory"]() as session:
        assert await check_sent_timeouts(session, now=NOW) == []
        assert await session.scalar(select(func.count(Alert.id))) == 0


# --- Срок одобрения 48 часов ------------------------------------------------------


async def test_approval_expires_after_48_hours(env):
    pid = await payment_id(env)
    async with env["factory"]() as session:
        payment = await session.get(ExpectedPayment, pid)
        payment.status_changed_at = NOW - timedelta(hours=49)
        await session.commit()

    async with env["factory"]() as session:
        expired = await expire_stale_approvals(session, now=NOW)
        assert [p.id for p in expired] == [pid]
        payment = await session.get(ExpectedPayment, pid)
        assert payment.status == EPS.EXPIRED
        alert = await session.scalar(select(Alert))
        assert "одобрение истекло" in alert.title.lower()
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "aml_approval_expired")
        )
        assert audit.entity_id == str(pid)
        await session.commit()


async def test_fresh_or_marked_sent_approval_does_not_expire(env):
    pid = await payment_id(env)
    async with env["factory"]() as session:
        # Свежее одобрение (47 ч) — не трогаем.
        payment = await session.get(ExpectedPayment, pid)
        payment.status_changed_at = NOW - timedelta(hours=47)
        await session.commit()
    async with env["factory"]() as session:
        assert await expire_stale_approvals(session, now=NOW) == []

        # Просроченное, но казначей отметил «отправил» — средства в пути,
        # контролирует таймер сценария 4, а не срок одобрения.
        payment = await session.get(ExpectedPayment, pid)
        payment.status_changed_at = NOW - timedelta(hours=60)
        payment.sent_marked_at = NOW - timedelta(minutes=5)
        await session.commit()
    async with env["factory"]() as session:
        assert await expire_stale_approvals(session, now=NOW) == []


async def test_resync_after_expiry_rescreens(env, tmp_path):
    pid = await payment_id(env)
    async with env["factory"]() as session:
        payment = await session.get(ExpectedPayment, pid)
        payment.status_changed_at = NOW - timedelta(hours=49)
        await expire_stale_approvals(session, now=NOW)
        await session.commit()

    # Повторная синхронизация инвойса: expired → pending_aml → скрининг заново.
    async with env["factory"]() as session:
        await apply_sync(session, batch(), aml_adapter=clean_adapter(tmp_path))
        payment = await session.get(ExpectedPayment, pid)
        assert payment.status == EPS.AML_APPROVED
        screenings = await session.scalar(select(func.count(AmlScreening.id)))
        assert screenings == 2  # первичная проверка + повторная после истечения
        await session.commit()


async def test_timers_disabled_by_zero_config(env, monkeypatch):
    monkeypatch.setattr(settings, "aml_approval_ttl_hours", 0)
    monkeypatch.setattr(settings, "aml_sent_timeout_minutes", 0)
    pid = await payment_id(env)
    async with env["factory"]() as session:
        payment = await session.get(ExpectedPayment, pid)
        payment.status_changed_at = NOW - timedelta(hours=500)
        payment.sent_marked_at = NOW - timedelta(hours=500)
        await session.commit()
    async with env["factory"]() as session:
        assert await expire_stale_approvals(session, now=NOW) == []
        assert await check_sent_timeouts(session, now=NOW) == []
