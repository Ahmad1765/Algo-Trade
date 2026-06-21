# ============================================================
# Multi-stage build.
# Stage 0: Build the Next.js static export (Node, discarded after).
# Stage 1: Build Python dependencies.
# Stage 2: Runtime — Python server + static site at /app/web.
# ============================================================

# Stage 0: Build the Next.js static export
FROM node:20-slim AS web-builder
WORKDIR /web
COPY frontend/ ./
# Tailwind v4 pulls in lightningcss / @tailwindcss-oxide, which ship per-platform
# native .node binaries. npm has a long-standing bug (npm/cli#4828) where, as long
# as a package-lock.json generated on another OS is present, even a fresh install
# silently skips the host-platform optional binary — breaking the linux build with
# "Cannot find lightningcss.linux-x64-gnu.node". Removing the lockfile forces npm
# to re-resolve optionalDependencies against THIS platform and download the binary.
RUN rm -rf node_modules package-lock.json .next out \
    && npm install --no-audit --no-fund \
    && npm run build
# Output: /web/out

# Stage 1: Python dependencies
FROM python:3.11-slim AS py-builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Runtime
FROM python:3.11-slim AS runtime

LABEL description="Algo-trade: Python trading engine with built-in dashboard"

RUN useradd -m -s /bin/bash appuser
WORKDIR /app

COPY --from=py-builder /install /usr/local
COPY src/ ./src/
COPY sample_data/ ./sample_data/
COPY scripts/ ./scripts/
COPY config.yaml.template ./config.yaml.template
RUN cp config.yaml.template config.yaml

COPY --from=web-builder /web/out ./web

RUN mkdir -p data logs && chown -R appuser:appuser /app

COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

USER appuser

# Health check hits the Python aiohttp server directly.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen('http://localhost:' + os.environ.get('PORT','8181') + '/health')"

EXPOSE 8181

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV CONFIG_PATH=/app/config.yaml
ENV MODE=paper
ENV API_PORT=8181

CMD ["./docker-entrypoint.sh"]
