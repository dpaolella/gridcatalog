# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY services ./services
RUN pip install --no-cache-dir ".[opensearch,postgres,queue,mcp,llm]"

COPY schemas ./schemas
COPY shapes ./shapes
COPY vocab ./vocab
COPY data ./data
COPY config ./config
COPY ops/alembic.ini ./ops/alembic.ini
COPY ops/migrations ./ops/migrations

RUN useradd --create-home --uid 10001 datahub && chown -R datahub /app
USER datahub

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -f http://localhost:8000/healthz || exit 1

CMD ["uvicorn", "datahub.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
