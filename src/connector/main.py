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
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text

from connector import __version__
from connector.config import settings
from connector.db import SessionFactory, engine
from connector.models import Base, Role, User
from connector.onec.api import router as onec_router
from connector.onec.sync import router as onec_sync_router
from connector.reports.api import router as reports_router
from connector.security import hash_password
from connector.seed import seed_defaults
from connector.web.accounting import router as accounting_router
from connector.web.api import router as admin_router
from connector.web.auth import router as auth_router
from connector.web.dashboard import router as dashboard_router
from connector.web.directory import router as directory_router
from connector.web.transactions import router as transactions_router
from connector.web.users import router as users_router

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
    if settings.seed_defaults:
        async with SessionFactory() as session:
            await seed_defaults(session)
            await session.commit()
    yield
    await engine.dispose()


app = FastAPI(
    title="Коннектор «Блокчейн → 1С»",
    version=__version__,
    lifespan=lifespan,
)
app.include_router(auth_router)
app.include_router(onec_router)
app.include_router(onec_sync_router)
app.include_router(admin_router)
app.include_router(users_router)
app.include_router(accounting_router)
app.include_router(transactions_router)
app.include_router(directory_router)
app.include_router(dashboard_router)
app.include_router(reports_router)


@app.get("/health")
async def health() -> dict:
    """Health-check для мониторинга (п. 10 ТЗ)."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok", "version": __version__}


# --- Веб-панель (React, web/dist) -------------------------------------------
# Catch-all регистрируется последним: /api, /health и /docs матчатся раньше.
_panel_dist = Path(settings.panel_dist_dir)
if _panel_dist.is_dir():
    app.mount("/assets", StaticFiles(directory=_panel_dist / "assets"), name="panel-assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        if path.startswith(("api/", "assets/")):
            raise HTTPException(status_code=404)
        return FileResponse(_panel_dist / "index.html")
