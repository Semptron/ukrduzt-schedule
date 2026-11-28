# syntax=docker/dockerfile:1.7
#
# Root-level Dockerfile — a safety net for the Render build.
#
# WHY THIS EXISTS
# The real Dockerfile + requirements.txt live under `backend/`. When Render's
# *Base Directory* (Root Directory) is set to the repo root (or left empty),
# a root-level `COPY requirements.txt .` fails with
#   error: failed to compute cache key: "/requirements.txt": not found
# because there is no requirements.txt at the repo root.
#
# The proper fix is `render.yaml` (rootDir: backend/) at the repo root, which
# Render honours on every deploy. This Dockerfile is the belt-and-suspenders
# fallback: if the dashboard Build Context points at the root, Render still
# finds a Dockerfile here, and it delegates into backend/ so the build is
# identical to building backend/Dockerfile directly.
#
# Prefer render.yaml. Only rely on this if the dashboard Dockerfile path is
# set to `Dockerfile` (root) and cannot be changed.

# Build the backend as if backend/ were the context. We copy backend/ in and
# run its Dockerfile via a multi-stage equivalent. Simplest reliable trick:
# just run the backend Dockerfile with backend/ as the context by re-issuing
# the same stages here, with paths prefixed by backend/.

# ---- Build stage: install deps into a clean layer ---------------------------
FROM python:3.12-slim AS builder

WORKDIR /app

COPY backend/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir --user -r requirements.txt

# ---- Runtime stage ----------------------------------------------------------
FROM python:3.12-slim AS runtime

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl tzdata \
 && ln -fs /usr/share/zoneinfo/Europe/Kyiv /etc/localtime \
 && echo "Europe/Kyiv" > /etc/timezone \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app

WORKDIR /app

# Installed deps from the builder stage.
COPY --from=builder --chown=app:app /root/.local /home/app/.local
# Application code — backend/ contents become /app.
COPY --chown=app:app backend/ .

RUN mkdir -p /app/data /app/downloads && chown -R app:app /app/data /app/downloads

ENV PATH=/home/app/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Kyiv

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
