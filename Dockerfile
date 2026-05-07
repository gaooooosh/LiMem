# ---------- Stage 1: build front-end SPA ----------
FROM node:20-alpine AS web-builder

WORKDIR /web

# 优先拷依赖清单以最大化层缓存
COPY web/package.json web/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

# 拷源码并构建
COPY web/ ./
RUN npm run build
# 产物位于 /web/dist


# ---------- Stage 2: python runtime ----------
FROM python:3.12-slim

WORKDIR /app

ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_DEFAULT_TIMEOUT=120 \
    UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

RUN pip install --no-cache-dir --retries 10 uv

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen || uv sync

RUN mkdir -p DB

COPY src/ src/

# 把前端构建产物放进 FastAPI 静态目录（与 service/routers/ui.py 中 UI_DIR 对齐）
COPY --from=web-builder /web/dist /app/src/service/static/ui

ENV PYTHONPATH=src

EXPOSE 8000

CMD ["uv", "run", "python", "-m", "service.main"]
