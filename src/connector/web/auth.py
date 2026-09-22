"""Вход в веб-панель: выдача токена по логину/паролю."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from connector.config import settings
from connector.db import get_session
from connector.models import AuditLog, User
from connector.security import TokenError, create_token, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class LoginOut(BaseModel):
    token: str
    role: str
    expires_at: datetime


@router.post("/login", response_model=LoginOut)
async def login(data: LoginIn, session: AsyncSession = Depends(get_session)) -> LoginOut:
    user = await session.scalar(select(User).where(User.username == data.username))
    # Единый ответ для «нет пользователя» и «неверный пароль» — не раскрываем логины.
    if user is None or not user.enabled or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    ttl = settings.token_ttl_hours * 3600
    try:
        token = create_token(user.id, user.role, settings.secret_key, ttl)
    except TokenError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from None
    session.add(
        AuditLog(actor=user.username, action="login", entity="user", entity_id=str(user.id))
    )
    await session.commit()
    return LoginOut(
        token=token,
        role=user.role.value,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl),
    )
