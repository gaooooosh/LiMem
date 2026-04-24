FROM python:3.12-slim

WORKDIR /app

RUN pip install uv

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen || uv sync

RUN mkdir -p DB

COPY src/ src/
COPY .env* ./

ENV PYTHONPATH=src

EXPOSE 8000

CMD ["uv", "run", "python", "-m", "service.main"]
