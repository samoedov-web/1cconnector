"""Управление пользователями панели (RBAC, п. 9 ТЗ).

Все операции — только администратор, кроме смены собственного пароля.
Защита от самоблокировки: нельзя отключить или разжаловать последнего
активного администратора.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from connector.db import get_session
from connector.models import AuditLog, Role, User
from connector.security import (
    CurrentUser,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)

router = APIRouter(prefix="/api/v1/users", tags=["users"])


class UserOut(BaseModel):
    id: int
    username: str
    role: Role
    enabled: bool


class UserCreateIn(BaseModel):
    username: str = Field(min_length=3, max_length=128)
    password: str = Field(min_length=8)
    role: Role


class UserPatchIn(BaseModel):
    role: Role | None = None
    enabled: bool | None = None
    password: str | None = Field(default=None, min_length=8)


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


def _out(user: User) -> UserOut:
    return UserOut(id=user.id, username=user.username, role=user.role, enabled=user.enabled)


async def _other_active_admins(session: AsyncSession, user_id: int) -> int:
    return await session.scalar(
        select(func.count(User.id)).where(
            User.role == Role.ADMIN, User.enabled, User.id != user_id
        )
    )


@router.get("", response_model=list[UserOut])
async def list_users(
    session: AsyncSession = Depends(get_session),
    actor: CurrentUser = Depends(require_admin),
) -> list[UserOut]:
    users = (await session.execute(select(User).order_by(User.id))).scalars().all()
    return [_out(u) for u in users]


@router.post("", response_model=UserOut, status_code=201)
async def create_user(
    data: UserCreateIn,
    session: AsyncSession = Depends(get_session),
    actor: CurrentUser = Depends(require_admin),
) -> UserOut:
    exists = await session.scalar(select(User.id).where(User.username == data.username))
    if exists is not None:
        raise HTTPException(status_code=409, detail="Пользователь уже существует")
    user = User(
        username=data.username,
        password_hash=hash_password(data.password),
        role=data.role,
    )
    session.add(user)
    await session.flush()
    session.add(
        AuditLog(
            actor=actor.username,
            action="user_created",
            entity="user",
            entity_id=str(user.id),
            details={"username": data.username, "role": data.role.value},
        )
    )
    await session.commit()
    return _out(user)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    data: UserPatchIn,
    session: AsyncSession = Depends(get_session),
    actor: CurrentUser = Depends(require_admin),
) -> UserOut:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    demotes_admin = user.role == Role.ADMIN and (
        data.enabled is False or (data.role is not None and data.role != Role.ADMIN)
    )
    if demotes_admin and await _other_active_admins(session, user.id) == 0:
        raise HTTPException(
            status_code=400,
            detail="Нельзя отключить или разжаловать последнего активного администратора",
        )

    changes: dict = {}
    if data.role is not None and data.role != user.role:
        changes["role"] = data.role.value
        user.role = data.role
    if data.enabled is not None and data.enabled != user.enabled:
        changes["enabled"] = data.enabled
        user.enabled = data.enabled
    if data.password is not None:
        changes["password"] = "changed"
        user.password_hash = hash_password(data.password)

    if changes:
        session.add(
            AuditLog(
                actor=actor.username,
                action="user_updated",
                entity="user",
                entity_id=str(user.id),
                details=changes,
            )
        )
    await session.commit()
    return _out(user)


@router.post("/me/password")
async def change_own_password(
    data: PasswordChangeIn,
    session: AsyncSession = Depends(get_session),
    actor: CurrentUser = Depends(get_current_user),
) -> dict:
    user = await session.get(User, actor.id)
    if user is None or not verify_password(data.current_password, user.password_hash):
        raise HTTPException(status_code=403, detail="Текущий пароль неверен")
    user.password_hash = hash_password(data.new_password)
    session.add(
        AuditLog(
            actor=actor.username,
            action="password_changed",
            entity="user",
            entity_id=str(user.id),
        )
    )
    await session.commit()
    return {"status": "changed"}
