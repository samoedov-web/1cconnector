"""Тесты управления пользователями: CRUD, RBAC, защита последнего админа."""

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from connector.config import settings
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
        poolclass=StaticPool,  # одно соединение — одна БД на все запросы
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
        admin = User(
            username="admin", password_hash=hash_password("admin-pass-123"), role=Role.ADMIN
        )
        session.add(admin)
        await session.commit()
        admin_id = admin.id

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "factory": factory, "admin_id": admin_id}

    app.dependency_overrides.clear()
    await engine.dispose()


def auth(user_id: int, role: Role) -> dict:
    return {"Authorization": "Bearer " + create_token(user_id, role, SECRET, 3600)}


async def test_admin_creates_and_lists_users(env):
    client, admin_id = env["client"], env["admin_id"]
    r = await client.post(
        "/api/v1/users",
        json={"username": "operator1", "password": "operator-pass", "role": "operator"},
        headers=auth(admin_id, Role.ADMIN),
    )
    assert r.status_code == 201
    assert r.json()["role"] == "operator"

    r = await client.get("/api/v1/users", headers=auth(admin_id, Role.ADMIN))
    assert [u["username"] for u in r.json()] == ["admin", "operator1"]

    # Дубль логина — 409.
    r = await client.post(
        "/api/v1/users",
        json={"username": "operator1", "password": "operator-pass", "role": "operator"},
        headers=auth(admin_id, Role.ADMIN),
    )
    assert r.status_code == 409


async def test_new_user_can_login_and_rbac_blocks_him(env):
    client, admin_id = env["client"], env["admin_id"]
    await client.post(
        "/api/v1/users",
        json={"username": "auditor1", "password": "auditor-pass", "role": "auditor"},
        headers=auth(admin_id, Role.ADMIN),
    )
    r = await client.post(
        "/api/v1/auth/login", json={"username": "auditor1", "password": "auditor-pass"}
    )
    assert r.status_code == 200
    token = r.json()["token"]

    # Аудитору можно читать очередь, но нельзя управлять пользователями.
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/api/v1/matching/pending", headers=headers)).status_code == 200
    assert (await client.get("/api/v1/users", headers=headers)).status_code == 403


async def test_disabled_user_loses_access_immediately(env):
    client, admin_id = env["client"], env["admin_id"]
    r = await client.post(
        "/api/v1/users",
        json={"username": "operator1", "password": "operator-pass", "role": "operator"},
        headers=auth(admin_id, Role.ADMIN),
    )
    op_id = r.json()["id"]
    op_headers = auth(op_id, Role.OPERATOR)
    assert (await client.get("/api/v1/matching/pending", headers=op_headers)).status_code == 200

    await client.patch(
        f"/api/v1/users/{op_id}", json={"enabled": False}, headers=auth(admin_id, Role.ADMIN)
    )
    # Токен ещё жив, но пользователь отключён — доступ отозван немедленно.
    assert (await client.get("/api/v1/matching/pending", headers=op_headers)).status_code == 401


async def test_last_admin_cannot_be_disabled_or_demoted(env):
    client, admin_id = env["client"], env["admin_id"]
    for payload in ({"enabled": False}, {"role": "auditor"}):
        r = await client.patch(
            f"/api/v1/users/{admin_id}", json=payload, headers=auth(admin_id, Role.ADMIN)
        )
        assert r.status_code == 400

    # Со вторым админом — можно.
    r = await client.post(
        "/api/v1/users",
        json={"username": "admin2", "password": "admin2-pass-123", "role": "admin"},
        headers=auth(admin_id, Role.ADMIN),
    )
    assert r.status_code == 201
    r = await client.patch(
        f"/api/v1/users/{admin_id}", json={"role": "auditor"}, headers=auth(admin_id, Role.ADMIN)
    )
    assert r.status_code == 200


async def test_change_own_password(env):
    client, admin_id = env["client"], env["admin_id"]
    r = await client.post(
        "/api/v1/users/me/password",
        json={"current_password": "wrong", "new_password": "new-pass-12345"},
        headers=auth(admin_id, Role.ADMIN),
    )
    assert r.status_code == 403

    r = await client.post(
        "/api/v1/users/me/password",
        json={"current_password": "admin-pass-123", "new_password": "new-pass-12345"},
        headers=auth(admin_id, Role.ADMIN),
    )
    assert r.status_code == 200
    r = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "new-pass-12345"}
    )
    assert r.status_code == 200
