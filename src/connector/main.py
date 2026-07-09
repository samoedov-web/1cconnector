"""Точка входа Core Service (FastAPI).

Отдаёт API веб-панели и API обмена с 1С. Индексер запускается отдельным
процессом (см. worker.py) — отдельный контейнер в docker-compose.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from connector import __version__
from connector.db import engine
from connector.models import Base
from connector.onec.api import router as onec_router
from connector.web.api import router as admin_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # MVP: схема создаётся напрямую; при первом релизе — миграции Alembic.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(
    title="Коннектор «Блокчейн → 1С»",
    version=__version__,
    lifespan=lifespan,
)
app.include_router(onec_router)
app.include_router(admin_router)


@app.get("/health")
async def health() -> dict:
    """Health-check для мониторинга (п. 10 ТЗ)."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok", "version": __version__}
