"""Точка входа Core Service (FastAPI).

Отдаёт API веб-панели, печатных форм и обмена с 1С. Индексер запускается
отдельным процессом (см. worker.py) — отдельный контейнер в docker-compose.

Схема БД в продакшене управляется миграциями Alembic (`alembic upgrade head`
в entrypoint контейнера); create_all в lifespan — страховка для локальной
разработки и тестов, на смигрированной базе это no-op.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import func, select, text

from connector import __version__
from connector.config import settings
from connector.db import SessionFactory, engine
from connector.models import Base, Role, User
from connector.onec.api import router as onec_router
from connector.reports.api import router as reports_router
from connector.security import hash_password
from connector.web.api import router as admin_router
from connector.web.auth import router as auth_router

log = logging.getLogger("connector.main")


async def bootstrap_admin() -> None:
    """Создать первого администратора, если пользователей ещё нет."""
    if not settings.admin_password:
        return
    async with SessionFactory() as session:
        count = await session.scalar(select(func.count(User.id)))
        if count:
            return
        session.add(
            User(
                username="admin",
                password_hash=hash_password(settings.admin_password),
                role=Role.ADMIN,
            )
        )
        await session.commit()
        log.info("Создан первый администратор панели: admin")


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await bootstrap_admin()
    yield
    await engine.dispose()


app = FastAPI(
    title="Коннектор «Блокчейн → 1С»",
    version=__version__,
    lifespan=lifespan,
)
app.include_router(auth_router)
app.include_router(onec_router)
app.include_router(admin_router)
app.include_router(reports_router)


@app.get("/health")
async def health() -> dict:
    """Health-check для мониторинга (п. 10 ТЗ)."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok", "version": __version__}
