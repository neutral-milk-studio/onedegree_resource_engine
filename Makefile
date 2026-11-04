.PHONY: setup test lint format mock openapi

setup:
	uv sync

test:
	uv run pytest

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
