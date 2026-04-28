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

ENV PYTHONPATH=src

EXPOSE 8000

CMD ["uv", "run", "python", "-m", "service.main"]
