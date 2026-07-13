"""Аутентификация веб-панели и RBAC (п. 9 ТЗ).

- Пароли: PBKDF2-SHA256 (stdlib, без внешних зависимостей).
- Токены: stateless, подписаны HMAC-SHA256 ключом CONNECTOR_SECRET_KEY,
  с истечением; отзыв доступа — отключением пользователя (User.enabled),
  проверяется на каждом запросе.
- Роли: администратор / оператор разбора / только чтение (аудитор).

Обмен с 1С аутентифицируется отдельным токеном (X-Exchange-Token, onec/api.py).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from connector.config import settings
from connector.db import get_session
from connector.models import Role, User

PBKDF2_ITERATIONS = 600_000


# --- Пароли -----------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, expected = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt.encode(), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


# --- Токены -----------------------------------------------------------------


class TokenError(Exception):
    pass


def _sign(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def create_token(user_id: int, role: Role, secret: str, ttl_seconds: int) -> str:
    if not secret:
        raise TokenError("Не задан CONNECTOR_SECRET_KEY — вход в панель невозможен")
    expires = int(time.time()) + ttl_seconds
    payload = f"{user_id}:{role.value}:{expires}".encode()
    return base64.urlsafe_b64encode(payload).decode() + "." + _sign(payload, secret)


@dataclass(frozen=True)
class TokenData:
    user_id: int
    role: Role
    expires: int


def parse_token(token: str, secret: str) -> TokenData:
    if not secret:
        raise TokenError("Не задан CONNECTOR_SECRET_KEY")
    try:
        encoded, signature = token.rsplit(".", 1)
        payload = base64.urlsafe_b64decode(encoded.encode())
    except (ValueError, binascii.Error):
        raise TokenError("Повреждённый токен") from None
    if not hmac.compare_digest(signature, _sign(payload, secret)):
        raise TokenError("Неверная подпись токена")
    try:
        user_id, role, expires = payload.decode().split(":")
        data = TokenData(int(user_id), Role(role), int(expires))
    except ValueError:
        raise TokenError("Повреждённый токен") from None
    if data.expires < time.time():
        raise TokenError("Срок действия токена истёк")
    return data


# --- FastAPI-зависимости -----------------------------------------------------


@dataclass(frozen=True)
class CurrentUser:
    id: int
    username: str
    role: Role


async def get_current_user(
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> CurrentUser:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Требуется Bearer-токен")
    try:
        data = parse_token(token, settings.secret_key)
    except TokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None
    user = await session.scalar(select(User).where(User.id == data.user_id))
    if user is None or not user.enabled:
        raise HTTPException(status_code=401, detail="Пользователь отключён")
    # Роль берём из БД, а не из токена: смена роли действует немедленно.
    return CurrentUser(id=user.id, username=user.username, role=user.role)


def require_roles(*roles: Role):
    async def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Недостаточно прав")
        return user

    return dependency


# Готовые зависимости под матрицу доступа панели.
require_admin = require_roles(Role.ADMIN)
require_operator = require_roles(Role.ADMIN, Role.OPERATOR)
require_reader = require_roles(Role.ADMIN, Role.OPERATOR, Role.AUDITOR)
