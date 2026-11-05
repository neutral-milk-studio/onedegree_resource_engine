.PHONY: setup test eval-suspicious lint format mock openapi up down ingest check api

setup:
	uv sync

test:
	uv run pytest

# Precision/recall of the suspicious-listing rules against data/eval/suspicious_cases.json.
eval-suspicious:
	uv run python -m maintenance.eval_suspicious

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

mock:
	uv run uvicorn contracts.mock_api:app --reload --port 8000

openapi:
	uv run python -m contracts.export_openapi

# --- Local stack -------------------------------------------------------------
up:
	docker compose up --build -d

down:
	docker compose down

# Ingest every configured source into the running stack's Postgres.
ingest:
	docker compose exec api uv run --no-sync python -m ingest.pipeline.run --all

# Run AI website checks for every record that has a sample page.
check:
	docker compose exec api uv run --no-sync python -m maintenance.worker.run_check

# Run the API on the host against a local Postgres (needs RE_DATABASE_URL).
api:
	uv run uvicorn api.app.main:app --reload --port 8000
