FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY alembic.ini ./
COPY migrations ./migrations
# Обновляемые шаблоны отчётности: монтируются volume-ом поверх этой копии,
# чтобы новые формы доставлялись без пересборки образа (п. 1.5 ТЗ).
COPY templates ./templates

ENV PYTHONUNBUFFERED=1

# API-контейнер: миграции схемы, затем сервис.
# Индексер запускается тем же образом с другой командой (после api).
CMD ["sh", "-c", "alembic upgrade head && uvicorn connector.main:app --host 0.0.0.0 --port 8000"]
