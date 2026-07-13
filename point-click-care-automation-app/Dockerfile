# Gateway PCC — SERVER (website + encrypted vault) container.
#
# This image runs ONLY the hosted server half of the project. The DESKTOP
# agent (Selenium + Firefox + proxy) is a per-user desktop app and is not
# containerised here.
#
# Build:   docker build -t gateway-pcc .
# Run:     docker run -d --name gateway-pcc \
#            -p 5000:5000 \
#            --env-file .env \
#            gateway-pcc
#
# State now lives in MongoDB (MONGODB_URI / MONGODB_DB) — no local volume needed.
# IMPORTANT: SECRET_KEY (in .env) is what decrypts stored PCC credentials, and
# FLASK_SECRET_KEY signs sessions. Keep .env safe and back SECRET_KEY up — losing
# it makes every stored credential undecryptable.

FROM python:3.12-slim AS base

# Runtime libs needed by opencv-python-headless (QR decoding).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
        libgl1 \
    && rm -rf /var/lib/apt/lists/*

# Install uv (the project's package manager) from the official image.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    # Bind to all interfaces so the published port is reachable.
    GATEWAY_HOST=0.0.0.0 \
    GATEWAY_PORT=5000

WORKDIR /app

# --- Dependency layer (cached unless pyproject/lock changes) ---------------
# Copy only the dependency manifests first for better build caching.
COPY pyproject.toml ./
# Copy the lockfile if present (optional — build still works without it).
COPY uv.* ./
RUN uv sync --no-install-project --no-dev || uv sync --no-dev

# --- Application layer -----------------------------------------------------
COPY backend ./backend
COPY frontend ./frontend
COPY wsgi.py ./
# Desktop installer served to signed-in users via /download/desktop-app.
COPY exe4endusers ./exe4endusers
# Bake the secrets/config into the image so the container reads them directly.
# WARNING: anyone who can pull this image from ACR can read .env (Mongo URI,
# admin password, encryption keys). Keep the registry private; rotate on leak.
COPY .env ./

EXPOSE 5000

# Put the uv-managed virtualenv on PATH so `gunicorn` runs directly, with no
# `uv run` wrapper. The wrapper forwards trailing args to the child process,
# which lets a platform-injected startup arg (e.g. Azure App Service passing a
# port) get exec'd as a bogus command. Calling the binary directly avoids that.
ENV PATH="/app/.venv/bin:$PATH"

# Gunicorn serves the WSGI app. wsgi.py initialises the DB + default admin on
# import, so the schema is ready before the first request.
#
# A small entrypoint script is used (not CMD) on purpose: Azure App Service
# appends an argument when it launches the container, and a bare CMD would be
# REPLACED by that argument (causing `exec: "5000": not found`). With an
# ENTRYPOINT script, anything Azure appends is ignored, and we read the port
# from $PORT (Azure sets it; default 5000 locally).
RUN printf '%s\n' \
    '#!/bin/sh' \
    'exec gunicorn --workers 3 --bind "0.0.0.0:${PORT:-5000}" --timeout 120 --access-logfile - wsgi:app' \
    > /app/entrypoint.sh && chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
