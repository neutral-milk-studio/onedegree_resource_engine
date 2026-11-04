# One image for the web tier and the scheduled jobs, built for ECS Fargate. Run from the repo root.
# Web: the default command. Jobs: python -m api.app.jobs.run import|maintenance|reindex.
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
COPY data ./data
COPY ingest ./ingest
COPY maintenance ./maintenance
COPY search ./search
COPY seed ./seed
COPY shared ./shared
COPY tools ./tools
COPY ui ./ui

RUN useradd -r -u 10001 app && mkdir -p /data && chown app /data
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "api.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
