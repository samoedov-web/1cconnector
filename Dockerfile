# Этап 1: сборка веб-панели (React + Vite)
FROM node:22-alpine AS panel
WORKDIR /panel
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web ./
RUN npm run build

# Этап 2: приложение
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY alembic.ini ./
COPY migrations ./migrations
# Диагностика внедрения: интеграционный прогон запускается из контейнера
# (docker compose run --rm api python scripts/integration_check.py ...)
COPY scripts ./scripts
# Обновляемые шаблоны отчётности: монтируются volume-ом поверх этой копии,
# чтобы новые формы доставлялись без пересборки образа (п. 1.5 ТЗ).
COPY templates ./templates
COPY --from=panel /panel/dist ./web/dist

ENV PYTHONUNBUFFERED=1

# API-контейнер: миграции схемы, затем сервис (панель отдаётся с корня).
# Индексер запускается тем же образом с другой командой (после api).
CMD ["sh", "-c", "alembic upgrade head && uvicorn connector.main:app --host 0.0.0.0 --port 8000"]
