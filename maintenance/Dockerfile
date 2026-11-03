# AI maintenance worker — container built for ECS Fargate (deploy optional).
FROM python:3.12-slim

WORKDIR /app

COPY maintenance/requirements.txt ./maintenance/requirements.txt
RUN pip install --no-cache-dir -r maintenance/requirements.txt || true

COPY contracts ./contracts
COPY shared ./shared
COPY seed ./seed
COPY maintenance ./maintenance

# Website source defaults to the sample implementation (no network) via env.
CMD ["python", "-m", "maintenance.worker.run_check"]
