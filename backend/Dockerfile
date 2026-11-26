# syntax=docker/dockerfile:1.7

# ---- Build stage: install deps into a clean layer ---------------------------
FROM python:3.12-slim AS builder

WORKDIR /app

# Build-time metadata ( OCI image labels )
ARG APP_VERSION=1.0.0
LABEL org.opencontainers.image.title="ukrduzt-schedule-api" \
      org.opencontainers.image.description="UkrDUZT correspondence schedule API" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.source="https://github.com/ukrduzt/ukrduzt-schedule"

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir --user -r requirements.txt

# ---- Runtime stage: small image, no compiler, non-root ----------------------
FROM python:3.12-slim AS runtime

# curl is needed for the HEALTHCHECK below.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# Non-root user: if the API has a bug, it can't write outside /app/data.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app

WORKDIR /app

# Copy installed deps from the builder stage...
COPY --from=builder --chown=app:app /root/.local /home/app/.local
# ...and the application code.
COPY --chown=app:app . .

# Runtime data goes here (mounted as a volume in compose).
RUN mkdir -p /app/data /app/downloads && chown -R app:app /app/data /app/downloads

ENV PATH=/home/app/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
