# syntax=docker/dockerfile:1
# Production backend image (Deployment-2A): one image for the API, Alembic, and every CLI job
# (intelligence-update, source-health, update-freshness, data-quality-audit).
#
# Layout: the backend is installed editable at /app/backend, the same source layout as a checkout,
# so Settings (repository-root .env, here /app/.env, which does not exist: configuration comes only
# from the container environment) and the update precheck (backend/alembic.ini) keep their
# source-tree-relative lookups. Dependencies are pinned by constraints.txt.
FROM python:3.12-slim-bookworm

ARG EXTRAS=""

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ENVIRONMENT=production \
    PATH=/opt/venv/bin:$PATH

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home \
       --shell /usr/sbin/nologin app

WORKDIR /app/backend
COPY pyproject.toml constraints.txt README.md ./
COPY src ./src
# EXTRAS is empty by default (deterministic operation needs no AI SDK); "[ai]" adds the optional
# Anthropic adapter at the constrained version.
RUN python -m venv /opt/venv \
    && pip install -c constraints.txt -e ".${EXTRAS}" \
    && pip check
COPY alembic.ini ./
COPY migrations ./migrations

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]
# No --reload. Only Caddy reaches this port (private Docker network), so forwarded headers from it
# are trusted for client addresses in the access log.
CMD ["uvicorn", "industry_intelligence.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
