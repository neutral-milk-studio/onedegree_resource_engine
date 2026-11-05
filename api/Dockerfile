# Build the UI: same-origin as the API (one deployed URL), so no API base and live mode.
FROM node:20-slim AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui ./
ENV VITE_API_MODE=live
ENV VITE_API_BASE=
RUN npm run build

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
COPY search ./search
COPY data/samples ./data/samples
COPY data/sample_websites ./data/sample_websites
COPY data/registry ./data/registry
COPY --from=ui /ui/dist ./ui/dist

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "api.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
