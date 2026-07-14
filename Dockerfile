# Stage 1: build the React dashboard bundle. Vite emits into
# /app/src/guiltyspark/web (outDir "../src/guiltyspark/web" relative to
# /app/frontend), which the Python stage copies into the package.
FROM node:20-slim AS web
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV PATH="/app/.venv/bin:/usr/local/bin:/root/.local/bin:/root/.codex/bin:${PATH}"
WORKDIR /app
RUN mkdir -p /workspace

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates git sqlite3 \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://chatgpt.com/codex/install.sh | CODEX_HOME=/opt/codex CODEX_NON_INTERACTIVE=1 sh \
    && codex_bin="$(find /opt/codex /root -type f -name codex -perm -111 2>/dev/null | head -n 1)" \
    && test -n "$codex_bin" \
    && ln -sf "$codex_bin" /usr/local/bin/codex \
    && codex --version

ENV CODEX_HOME=/data/codex

COPY pyproject.toml uv.lock README.md targets.example.toml ./
COPY src ./src
# Bundled dashboard from the web stage (src/guiltyspark/web is .dockerignored,
# so this is the only copy that lands in the image — before uv sync so the
# built wheel includes it).
COPY --from=web /app/src/guiltyspark/web ./src/guiltyspark/web
COPY knowledge ./knowledge
COPY docker-entrypoint.sh /usr/local/bin/guiltyspark-entrypoint

RUN uv sync --locked --no-dev --no-editable
RUN chmod +x /usr/local/bin/guiltyspark-entrypoint

VOLUME ["/data"]
ENTRYPOINT ["guiltyspark-entrypoint"]
CMD ["guiltyspark", "daemon"]
