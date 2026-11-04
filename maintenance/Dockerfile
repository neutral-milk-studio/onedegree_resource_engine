# AI maintenance worker, built for ECS Fargate. Run from the repository root.
FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.5.31

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY contracts ./contracts
COPY shared ./shared
COPY seed ./seed
COPY maintenance ./maintenance

# The website source defaults to the sample implementation (no network).
CMD ["uv", "run", "--no-sync", "python", "-m", "maintenance.worker.run_check"]
