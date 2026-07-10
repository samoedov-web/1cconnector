"""Фаза 6 depository-спеки: режимы custody_mode (off / shadow / active)."""

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from connector.config import Settings, settings
from connector.db import get_session
from connector.main import app
from connector.models import Base, Role, User
from connector.security import create_token, hash_password

SECRET = "test-secret"


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
    async with factory() as session:
        operator = User(username="op", password_hash=hash_password("x" * 8),
                        role=Role.OPERATOR)
        session.add(operator)
        await session.commit()
        operator_id = operator.id

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test",
        headers={"Authorization": "Bearer "
                 + create_token(operator_id, Role.OPERATOR, SECRET, 3600)},
    ) as client:
        yield {"client": client, "monkeypatch": monkeypatch}

    app.dependency_overrides.clear()
    await engine.dispose()


GATED = [
    ("GET", "/api/v1/reconciliation/sources"),
    ("POST", "/api/v1/reconciliation/fetch"),
    ("POST", "/api/v1/reconciliation/runs"),
    ("GET", "/api/v1/reconciliation/runs"),
    ("GET", "/api/v1/reconciliation/runs/1"),
    ("POST", "/api/v1/reconciliation/results/1/resolve"),
    ("GET", "/api/v1/reports/custody-reconciliation/1"),
]


async def test_off_is_default_and_gates_everything(env):
    client = env["client"]
    assert settings.custody_mode == "off"  # регрессия: по умолчанию выключено

    r = await client.get("/api/v1/reconciliation/mode")
    assert r.json()["mode"] == "off"
    assert "CONNECTOR_CUSTODY_MODE=shadow" in r.json()["message"]

    for method, url in GATED:
        r = await client.request(method, url, json={} if method == "POST" else None)
        assert r.status_code == 403, (method, url)
        assert "custody_mode=off" in r.json()["detail"]


async def test_active_is_explicit_stub_with_adr_link(env):
    client = env["client"]
    env["monkeypatch"].setattr(settings, "custody_mode", "active")

    r = await client.get("/api/v1/reconciliation/mode")
    assert r.json()["mode"] == "active"

    for method, url in GATED:
        r = await client.request(method, url, json={} if method == "POST" else None)
        assert r.status_code == 501, (method, url)
        detail = r.json()["detail"]
        assert "ADR-001-custody-first" in detail  # ссылка на дизайн-заметку
        assert "shadow" in detail  # подсказано действие


async def test_shadow_enables_layer(env, tmp_path, monkeypatch):
    client = env["client"]
    monkeypatch.setattr(settings, "custody_mode", "shadow")
    monkeypatch.setattr(settings, "custody_fixtures_dir", str(tmp_path))

    assert (await client.get("/api/v1/reconciliation/runs")).status_code == 200
    r = await client.get("/api/v1/reconciliation/sources")
    assert r.status_code == 200
    assert r.json()["active"] == "mock-depo"


def test_unknown_mode_is_startup_error(monkeypatch):
    monkeypatch.setenv("CONNECTOR_CUSTODY_MODE", "shadwo")  # опечатка
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
