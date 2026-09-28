FROM python:3.12-slim

WORKDIR /app

ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PYTHONPATH=/app/src \
    HOST=0.0.0.0 \
    PORT=8000

RUN pip install --no-cache-dir uv==0.10.4

COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project \
      --format requirements-txt --output-file /tmp/requirements.txt \
    && pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY src/ src/
COPY config/ config/
COPY docs/service/ docs/service/

EXPOSE 8000

CMD ["python", "-m", "limem.service.main"]
