"""Конфигурация сервиса (env / .env, on-premise)."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CONNECTOR_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://connector:connector@db:5432/connector"

    # Аутентификация обмена с 1С — токен фонового задания синхронизации.
    onec_exchange_token: str = Field(default="", description="Токен для HTTP-обмена с 1С")

    # Порог финальности per-network (учётная финальность = договорная финальность,
    # см. п. 3 ТЗ). Переопределяется в таблице networks.
    default_finality: dict[str, int] = {"tron": 19, "ethereum": 12}

    # Интервал опроса индексера, сек.
    indexer_poll_interval: int = 60

    # Допуск автоматчинга «сумма ± допуск» (доля от суммы инвойса).
    matching_amount_tolerance: str = "0.005"

    # Требовать подтверждения минимум от двух источников данных на сеть.
    cross_check_sources: bool = True


settings = Settings()
