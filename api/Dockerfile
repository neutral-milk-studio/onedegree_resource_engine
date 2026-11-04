# API service (also runs on-demand ingest). Run from the repository root.
FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.5.31

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY contracts ./contracts
COPY shared ./shared
COPY seed ./seed
COPY config ./config
COPY ingest ./ingest
COPY api ./api
COPY maintenance ./maintenance
COPY data/samples ./data/samples
COPY data/sample_websites ./data/sample_websites

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "api.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
