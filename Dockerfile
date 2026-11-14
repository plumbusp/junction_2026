# syntax=docker/dockerfile:1
#
# Spot the Spin: production image.
# Build context is filtered by .dockerignore (allowlist), so .env, secrets/,
# .git, docs and data never reach any stage of this build.

ARG PYTHON_IMAGE=python:3.12-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.8

# ---------------------------------------------------------------------------
# uv binary, pinned to the version used locally
# ---------------------------------------------------------------------------
FROM ${UV_IMAGE} AS uv

# ---------------------------------------------------------------------------
# Builder: resolve and install runtime dependencies from uv.lock into /opt/venv
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build
COPY pyproject.toml uv.lock ./

# --frozen: fail if uv.lock is out of date instead of silently re-resolving.
# --no-dev: pytest and ruff stay out of the image.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ---------------------------------------------------------------------------
# Runtime: slim Python, non-root user, only the venv and the app source
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app/backend \
    DB_PATH=/app/var/app.db \
    HEALTHCHECK_PATHS="/health /api/health"

# tzdata: the daily job schedules at 00:00 in DAILY_TZ (zoneinfo needs system data here).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home \
       --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

# The allowlist in .dockerignore limits this to backend/ (without tests),
# frontend/, data/dev/ (few-shot examples) plus pyproject.toml and uv.lock.
# The repo layout is kept under /app because backend/app/config.py resolves
# paths relative to the repo root.
COPY --chown=app:app . /app/

# /app/var is the mount point of the named SQLite volume. It is deliberately
# not /app/data: a volume there would hide the data/dev/ shipped in each new
# image behind the copy made on first start. Creating it here with the right
# owner makes a fresh volume writable by the non-root user.
#
# The paid verdict cache (data/cache) must be writable and survive redeploys, so it
# lives in the volume too: /app/data/cache is a symlink to /app/var/cache.
RUN mkdir -p /app/var/cache \
    && ln -s /app/var/cache /app/data/cache \
    && chown -R app:app /app/var \
    && chown -h app:app /app/data/cache

USER app

EXPOSE 8000

# Slim images have no curl, so the check uses the standard library only.
# Healthy when any path in HEALTHCHECK_PATHS answers 200.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, sys, urllib.request\nfor p in os.environ.get('HEALTHCHECK_PATHS', '/health').split():\n    try:\n        if urllib.request.urlopen('http://127.0.0.1:8000' + p, timeout=4).status == 200:\n            sys.exit(0)\n    except Exception:\n        pass\nsys.exit(1)"]

# One worker: SQLite is the database. Traefik is the only client, so proxy
# headers from it are trusted (the container publishes no ports).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
