# Build context is labs/:  docker build -f labs/manager/Dockerfile labs
FROM node:22.12-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/Shashkist/Learning_factory"
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
COPY manager/pyproject.toml manager/uv.lock ./
RUN --mount=from=ghcr.io/astral-sh/uv:0.7.8,source=/uv,target=/bin/uv \
    uv sync --frozen --no-dev --no-install-project
COPY manager/app ./app
COPY manager/migrations ./migrations
COPY --from=web /web/dist /srv/web
COPY content /srv/content
ENV LF_WEB_DIR=/srv/web LF_CONTENT_DIR=/srv/content
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
