# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# Stage 1 — build the web UI with Node, so the runtime image needs no toolchain
# ---------------------------------------------------------------------------
FROM node:22-alpine AS web

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

COPY frontend/ ./
# vite.config.ts writes to ../nautilus/webui/dist → /build/nautilus/webui/dist
RUN npm run build


# ---------------------------------------------------------------------------
# Stage 2 — runtime: Python + a JRE, because the panel launches real servers
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NAUTILUS_HOST=0.0.0.0 \
    NAUTILUS_PORT=8199 \
    NAUTILUS_DATA_DIR=/data

# Java is the actual workload: the panel only supervises it.
# Prefer JDK 21 (Minecraft 1.21+); fall back to whatever Debian defaults to.
RUN apt-get update \
 && (apt-get install -y --no-install-recommends openjdk-21-jre-headless \
     || apt-get install -y --no-install-recommends default-jre-headless) \
 && apt-get install -y --no-install-recommends curl tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY nautilus ./nautilus
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install .

COPY --from=web /build/nautilus/webui/dist ./nautilus/webui/dist

# The panel manages child processes; tini reaps them and forwards signals.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "nautilus"]

VOLUME ["/data"]
EXPOSE 8199

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8199/api/meta > /dev/null || exit 1
