# CryptoVED 1C Connector v2.1

**Статус:** Ready for Methodologist Review  
**Ветка:** `aml/owner-decisions`  
**Дата релиза:** 2026-09-18

## Описание
Система автоматизации валютного контроля и налогового учета операций с цифровыми финансовыми активами (ЦФА). Интегрирует блокчейн-данные, AML-скрининг, депозитарную сверку и учет в 1С:Предприятие.

## Архитектура
Система построена по принципу **Read-Only**:
- ❌ Нет хранения приватных ключей
- ❌ Нет подписания транзакций
- ❌ Нет отправки платежей
- ✅ Только наблюдение за блокчейном
- ✅ Сбор доказательной базы (Evidence Vault)
- ✅ Автоматический комплаенс (AML + Registry + Issuer)
- ✅ Налоговый учет (ФИФО, ст. 282.3 НК РФ)
- ✅ Обмен документами с 1С

## Реализованные этапы (Roadmap v2.1)
| Этап | Компонент | Статус | Файлы |
|------|-----------|--------|-------|
| Stage 1 | Custody Data Foundation | ✅ PASS | `src/connector/custody/`, миграции |
| Stage 2 | Operation Lifecycle | ✅ PASS | `src/connector/services/operation_service.py` |
| Stage 3 | Registry & Issuer Adapters | ✅ PASS | `src/connector/adapters/stage3/` |
| Stage 4 | Evidence Vault | ✅ PASS | `src/connector/services/evidence_service.py` |
| Stage 5 | Compliance Approval Gate | ✅ PASS | Логика шлюза в `OperationService` |
| Stage 6 | Web UI & Acceptance Tests | ✅ PASS | `src/web/src/components/`, `tests/test_acceptance_whitepaper.py` |
| Stage 7 | 1C Integration & Tax | ✅ PASS | `src/connector/onec/`, `src/connector/reports/tax_registry.py` |
| Stage 8 | Regulatory Reporting | ✅ PASS | `src/connector/reports/regulatory.py` |
| Stage 9 | Production Hardening | ✅ PASS | `src/connector/monitoring/`, алерты |
| Stage 10 | Documentation | ✅ PASS | Этот файл, `docs/` |

## Быстрый старт
1. Установите зависимости: `poetry install`
2. Настройте БД: `export DATABASE_URL=postgresql://...`
3. Примените миграции: `alembic upgrade head`
4. Загрузите фикстуры: `python scripts/load_fixtures.py`
5. Запустите тесты: `pytest -v`

## Безопасность
- Все приватные данные хранятся в зашифрованном виде.
- Действия пользователей логируются в `AuditLog`.
- Первичные данные (транзакции, выписки) неизменяемы (Append-Only).

## Лицензия
Commercial License (см. LICENSE)
