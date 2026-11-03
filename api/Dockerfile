# API service — FastAPI, container built for ECS Fargate (deploy optional).
FROM python:3.12-slim

WORKDIR /app

# Dependencies first for layer caching. requirements.txt is added by the API stream.
COPY api/requirements.txt ./api/requirements.txt
RUN pip install --no-cache-dir -r api/requirements.txt || true

# Shared libs + contracts + the API app + seed (read by some endpoints/tests).
COPY contracts ./contracts
COPY shared ./shared
COPY seed ./seed
COPY api ./api

EXPOSE 8000
CMD ["uvicorn", "api.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
