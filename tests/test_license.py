"""Тесты лицензирования: подпись, статусы, пакеты, лимиты, гейт обмена с 1С."""

import base64
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from connector import license as license_module
from connector.config import settings
from connector.db import get_session
from connector.license import TIER_PRESETS, canonical_payload, load_state
from connector.main import app
from connector.models import Base, Network, Organization, Role, User
from connector.security import create_token, hash_password

NOW = datetime(2026, 7, 10, tzinfo=timezone.utc)
SECRET = "test-secret"


def keypair():
    private = Ed25519PrivateKey.generate()
    public_hex = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    return private, public_hex


def write_license(tmp_path, private, tier="business", valid_until=None, grace_days=14,
                  overrides=None):
    payload = {
        "license_id": "test",
        "tier": tier,
        "issued_to": "ООО «Вектор Трейд»",
        "valid_until": (valid_until or NOW + timedelta(days=100)).isoformat(),
        "grace_days": grace_days,
        **TIER_PRESETS[tier],
        **(overrides or {}),
    }
    document = {
        "payload": payload,
        "signature": base64.b64encode(private.sign(canonical_payload(payload))).decode(),
    }
    path = tmp_path / "license.json"
    path.write_text(json.dumps(document, ensure_ascii=False))
    return str(path)


# --- Подпись и статусы --------------------------------------------------------


def test_valid_license_roundtrip(tmp_path):
    private, public_hex = keypair()
    path = write_license(tmp_path, private, tier="business")
    state = load_state(path, public_hex, now=NOW)
    assert state.status == "valid"
    assert state.tier == "business"
    assert state.max_wallets == 10
    assert state.report_updates is True
    assert state.sync_allowed


def test_tampered_payload_rejected(tmp_path):
    private, public_hex = keypair()
    path = write_license(tmp_path, private, tier="start")
    document = json.loads(open(path).read())
    document["payload"]["max_wallets"] = 100_000  # подкрутили лимит
    open(path, "w").write(json.dumps(document, ensure_ascii=False))
    state = load_state(path, public_hex, now=NOW)
    assert state.status == "invalid"
    assert not state.sync_allowed
    assert state.max_wallets == 0


def test_wrong_key_rejected(tmp_path):
    private, _ = keypair()
    _, other_public = keypair()
    path = write_license(tmp_path, private)
    assert load_state(path, other_public, now=NOW).status == "invalid"


def test_missing_file_is_demo(tmp_path):
    state = load_state(str(tmp_path / "nope.json"), "irrelevant", now=NOW)
    assert state.status == "demo"
    assert state.sync_allowed
    assert state.max_wallets == 1
    assert state.max_organizations == 1
    assert state.report_updates is False


def test_grace_then_expired(tmp_path):
    private, public_hex = keypair()
    path = write_license(
        tmp_path, private, valid_until=NOW - timedelta(days=5), grace_days=14
    )
    grace = load_state(path, public_hex, now=NOW)
    assert grace.status == "grace"
    assert grace.sync_allowed  # льготный период: синхронизация ещё работает

    expired = load_state(path, public_hex, now=NOW + timedelta(days=20))
    assert expired.status == "expired"
    assert not expired.sync_allowed  # чтение доступно, синхронизация — нет


def test_tier_presets_matrix():
    assert TIER_PRESETS["start"] == {
        "max_organizations": 1, "max_wallets": 3, "report_updates": False,
    }
    assert TIER_PRESETS["business"] == {
        "max_organizations": 3, "max_wallets": 10, "report_updates": True,
    }
    assert TIER_PRESETS["holding"] == {
        "max_organizations": None, "max_wallets": None, "report_updates": True,
    }


def test_holding_is_unlimited(tmp_path):
    private, public_hex = keypair()
    path = write_license(tmp_path, private, tier="holding")
    state = load_state(path, public_hex, now=NOW)
    assert state.max_wallets is None
    assert state.max_organizations is None


# --- Enforcement через API -----------------------------------------------------


@pytest.fixture
async def env(monkeypatch, tmp_path):
    private, public_hex = keypair()
    license_path = write_license(
        tmp_path, private, tier="start"  # 1 юрлицо, 3 кошелька
    )
    monkeypatch.setattr(settings, "secret_key", SECRET)
    monkeypatch.setattr(settings, "license_path", license_path)
    monkeypatch.setattr(settings, "license_public_key", public_hex)
    monkeypatch.setattr(settings, "onec_exchange_token", "exch-token")
    license_module.reset_cache()

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
        admin = User(username="admin", password_hash=hash_password("x" * 8), role=Role.ADMIN)
        network = Network(code="tron", name="TRON", finality_depth=19)
        org = Organization(name="Основная организация")
        session.add_all([admin, network, org])
        await session.commit()
        admin_id = admin.id

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": "Bearer " + create_token(admin_id, Role.ADMIN, SECRET, 3600)},
    ) as client:
        yield {"client": client, "private": private, "license_path": license_path}

    app.dependency_overrides.clear()
    license_module.reset_cache()
    await engine.dispose()


async def test_wallet_limit_enforced(env):
    client = env["client"]
    for i in range(3):
        r = await client.post(
            "/api/v1/wallets", json={"network_code": "tron", "address": f"T{i}"}
        )
        assert r.status_code == 200
    r = await client.post("/api/v1/wallets", json={"network_code": "tron", "address": "T4"})
    assert r.status_code == 403
    assert "Старт" in r.json()["detail"]


async def test_organization_limit_enforced(env):
    client = env["client"]
    r = await client.post("/api/v1/organizations", json={"name": "ООО «Второе»"})
    assert r.status_code == 403  # пакет «старт»: 1 юрлицо уже есть


async def test_license_status_endpoint(env):
    client = env["client"]
    r = await client.get("/api/v1/dashboard/license")
    data = r.json()
    assert data["tier_title"] == "Старт"
    assert data["status"] == "valid"
    assert data["usage"]["max_wallets"] == 3
    assert data["report_updates"] is False


async def test_exchange_blocked_when_expired(env, tmp_path):
    client = env["client"]
    headers = {"X-Exchange-Token": "exch-token"}
    r = await client.get("/api/v1/onec/documents", headers=headers)
    assert r.status_code == 200  # лицензия действует

    # Перевыпускаем лицензию задним числом: истекла, grace прошёл.
    write_license(
        tmp_path, env["private"],
        valid_until=datetime.now(timezone.utc) - timedelta(days=60), grace_days=14,
    )
    license_module.reset_cache()
    r = await client.get("/api/v1/onec/documents", headers=headers)
    assert r.status_code == 402
    assert "истёк" in r.json()["detail"]
    # Чтение в панели при этом работает.
    assert (await client.get("/api/v1/wallets")).status_code == 200
