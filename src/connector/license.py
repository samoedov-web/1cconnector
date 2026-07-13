"""Лицензирование (п. 10 ТЗ): годовая лицензия, тиры, grace-режим.

Три пакета (лимиты зашиты в подписанный файл — тарифы меняются без релиза):

| Пакет   | Юр. лица | Кошельки | Обновления форм отчётности |
|---------|----------|----------|----------------------------|
| старт   | 1        | 3        | нет                        |
| бизнес  | 3        | 10       | да                         |
| холдинг | безлимит | безлимит | да                         |

Механика:
- файл license.json = {"payload": {...}, "signature": base64(Ed25519)};
  подпись — над канонизированным JSON payload; закрытый ключ у вендора
  (scripts/license_tool.py), открытый — в настройках коннектора;
- статусы: valid → grace (истёк срок, но идёт льготный период — синхронизация
  работает, панель предупреждает) → expired (синхронизация и обмен с 1С
  остановлены, чтение/панель/отчёты доступны — данные клиента не запираются);
- без файла лицензии — деморежим: всё работает с лимитом 1 юрлицо / 1 кошелёк
  (достаточно для пилота, бесполезно в проде);
- повреждённый файл/подпись — invalid: как expired, с понятной причиной.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from connector.config import settings

log = logging.getLogger("connector.license")

TIER_PRESETS: dict[str, dict] = {
    "start": {"max_organizations": 1, "max_wallets": 3, "report_updates": False},
    "business": {"max_organizations": 3, "max_wallets": 10, "report_updates": True},
    "holding": {"max_organizations": None, "max_wallets": None, "report_updates": True},
}
TIER_TITLES = {"start": "Старт", "business": "Бизнес", "holding": "Холдинг", "demo": "Демо"}

DEMO_LIMITS = {"max_organizations": 1, "max_wallets": 1, "report_updates": False}


def canonical_payload(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


@dataclass(frozen=True)
class LicenseState:
    status: str  # valid | grace | expired | demo | invalid
    tier: str
    issued_to: str
    valid_until: datetime | None
    grace_until: datetime | None
    max_organizations: int | None  # None = безлимит
    max_wallets: int | None
    report_updates: bool
    reason: str = ""

    @property
    def sync_allowed(self) -> bool:
        """Индексация и обмен с 1С разрешены. Чтение доступно всегда."""
        return self.status in ("valid", "grace", "demo")

    @property
    def tier_title(self) -> str:
        return TIER_TITLES.get(self.tier, self.tier)


def _demo(now: datetime) -> LicenseState:
    return LicenseState(
        status="demo",
        tier="demo",
        issued_to="",
        valid_until=None,
        grace_until=None,
        **DEMO_LIMITS,
    )


def _invalid(reason: str) -> LicenseState:
    return LicenseState(
        status="invalid",
        tier="—",
        issued_to="",
        valid_until=None,
        grace_until=None,
        max_organizations=0,
        max_wallets=0,
        report_updates=False,
        reason=reason,
    )


def load_state(
    path: str, public_key_hex: str, now: datetime | None = None
) -> LicenseState:
    """Прочитать, проверить подпись и оценить лицензию на момент now."""
    now = now or datetime.now(timezone.utc)
    file = Path(path)
    if not file.is_file():
        return _demo(now)
    if not public_key_hex:
        return _invalid("Файл лицензии есть, но не задан CONNECTOR_LICENSE_PUBLIC_KEY")

    try:
        document = json.loads(file.read_text(encoding="utf-8"))
        payload = document["payload"]
        signature = base64.b64decode(document["signature"])
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        public_key.verify(signature, canonical_payload(payload))
    except InvalidSignature:
        return _invalid("Подпись лицензии не прошла проверку")
    except (KeyError, ValueError, TypeError, binascii.Error, json.JSONDecodeError) as exc:
        return _invalid(f"Файл лицензии повреждён: {exc}")

    try:
        valid_until = datetime.fromisoformat(payload["valid_until"])
        if valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=timezone.utc)
        grace_days = int(payload.get("grace_days", 14))
        grace_until = valid_until + timedelta(days=grace_days)
        tier = str(payload.get("tier", "start"))
        state = LicenseState(
            status="valid",
            tier=tier,
            issued_to=str(payload.get("issued_to", "")),
            valid_until=valid_until,
            grace_until=grace_until,
            max_organizations=payload.get("max_organizations"),
            max_wallets=payload.get("max_wallets"),
            report_updates=bool(payload.get("report_updates", False)),
        )
    except (KeyError, ValueError) as exc:
        return _invalid(f"Некорректные поля лицензии: {exc}")

    if now <= valid_until:
        return state
    if now <= grace_until:
        return LicenseState(**{**state.__dict__, "status": "grace"})
    return LicenseState(
        **{
            **state.__dict__,
            "status": "expired",
            "reason": f"Срок лицензии истёк {valid_until:%d.%m.%Y}",
        }
    )


def _reevaluate(state: LicenseState, now: datetime) -> LicenseState:
    """Пересчитать только статус по времени (файл не перечитывается)."""
    if state.valid_until is None:  # demo / invalid — от времени не зависят
        return state
    if now <= state.valid_until:
        status = "valid"
    elif state.grace_until is not None and now <= state.grace_until:
        status = "grace"
    else:
        return LicenseState(
            **{
                **state.__dict__,
                "status": "expired",
                "reason": f"Срок лицензии истёк {state.valid_until:%d.%m.%Y}",
            }
        )
    return LicenseState(**{**state.__dict__, "status": status, "reason": ""})


# Кэш по mtime файла: воркер дергает состояние каждый цикл, панель — на
# каждый запрос; замена файла лицензии подхватывается без перезапуска.
_cache: tuple[float | None, str, LicenseState] | None = None


def current_state(now: datetime | None = None) -> LicenseState:
    global _cache
    now = now or datetime.now(timezone.utc)
    file = Path(settings.license_path)
    mtime = file.stat().st_mtime if file.is_file() else None
    key = settings.license_public_key
    if _cache is not None and _cache[0] == mtime and _cache[1] == key:
        return _reevaluate(_cache[2], now)
    state = load_state(settings.license_path, key, now)
    _cache = (mtime, key, state)
    return state


def reset_cache() -> None:
    global _cache
    _cache = None
