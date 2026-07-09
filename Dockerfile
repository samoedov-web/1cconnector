FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1

# API-контейнер; индексер запускается тем же образом с другой командой
CMD ["uvicorn", "connector.main:app", "--host", "0.0.0.0", "--port", "8000"]
