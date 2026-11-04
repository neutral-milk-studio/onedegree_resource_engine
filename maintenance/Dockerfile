# AI maintenance worker for local Compose runs. On AWS the shared image in api/Dockerfile runs the
# same job. Run from the repository root.
FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.5.31

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 DATA_DIR=/data

COPY api ./api
COPY config ./config
COPY content ./content
COPY contracts ./contracts
COPY ingest ./ingest
COPY maintenance ./maintenance
COPY seed ./seed
COPY shared ./shared

RUN useradd -r -u 10001 app && mkdir -p /data && chown app /data
USER app

CMD ["python", "-m", "maintenance.worker.run_check"]
