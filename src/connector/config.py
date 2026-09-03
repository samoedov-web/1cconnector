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

    # --- Веб-панель: аутентификация и RBAC (п. 9 ТЗ) ---
    # Ключ подписи токенов панели; без него вход в панель невозможен.
    secret_key: str = ""
    token_ttl_hours: int = 12
    # Пароль первого администратора: если задан и пользователей нет,
    # при старте создаётся учётка admin (bootstrap первого входа).
    admin_password: str = ""

    # --- Печатные формы (п. 7 ТЗ) ---
    # Каталог обновляемых шаблонов отчётности — обновляется отдельно от ядра.
    report_templates_dir: str = "templates/reports"

    # Сборка веб-панели (React); если каталога нет — отдаётся только API.
    panel_dist_dir: str = "web/dist"

    # Начальное заполнение справочников сетей/активов при пустой базе.
    seed_defaults: bool = True

    # Webhook алертов мониторинга (POST JSON); пусто — только журнал в БД.
    alert_webhook_url: str = ""

    # --- AML (specs/aml-adapter.md) ---
    # Провайдер скрининга адресов (id в реестре aml-источников) и его
    # конфигурация; до подключения реального провайдера — мок с фикстурами.
    aml_source_id: str = "mock-aml"
    aml_fixtures_path: str = "fixtures/aml.json"
    # Реальный провайдер (aml_source_id="crystal"): ключ из кабинета
    # клиента; base_url пустой — дефолт адаптера.
    aml_api_key: str = ""
    aml_base_url: str = ""
    # Пороги решений по risk_score (шаг 3 регламента): 0..approved — можно
    # отправлять; ..review — решение комплаенс-офицера; выше — запрещено.
    aml_approved_max_score: int = 30
    aml_review_max_score: int = 70
    # Ре-скрининг справочника адресов по расписанию (шаг 3.3 aml-спеки);
    # 0 — отключён. Смена статуса по порогам → алерт.
    aml_rescreen_hours: int = 24
    # Решения владельца от 2026-07-14:
    # срок действия AML-одобрения — 48 часов, затем expired и повторная
    # проверка (вопрос 7.1 спеки); 0 — бессрочно.
    aml_approval_ttl_hours: int = 48
    # Таймер сценария 4 регламента: казначей отметил «отправил», а
    # транзакция не обнаружена за N минут → алерт; 0 — отключён.
    aml_sent_timeout_minutes: int = 30

    # --- Политики платежей (G6) ---
    # Блокировка сценариев платежей: domestic_payment — внутренний платёж (ACC-18-050)
    blocked_payment_scenarios: list[str] = Field(default_factory=list)  # e.g. ["domestic_payment"]
    # Дата доступности прямого маршрута (ACC-18-051): прямой маршрут доступен с 1.09.2026
    direct_route_available_from: str = "2026-09-01"  # ISO date
    # Внутренние таймеры (не ст. 45): 48 ч для внутренних проверок, 30 мин — SLA (ACC-18-052)
    internal_check_hours: int = 48
    internal_sla_minutes: int = 30

    # --- Лицензирование (п. 10 ТЗ) ---
    # Файл лицензии (выпускается scripts/license_tool.py вендора);
    # отсутствует — деморежим (1 юрлицо, 1 кошелёк, без обновлений форм).
    license_path: str = "license.json"
    # Открытый ключ Ed25519 (hex) для проверки подписи лицензии.
    license_public_key: str = ""


settings = Settings()
