.PHONY: setup test lint format demo openapi transform-test transform-report

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

demo:
	uv run python -m api.app.demo --reset

openapi:
	uv run python -m contracts.export_openapi

# Transform layer: fast tests, and an HTML report of a run on the sample pages.
transform-test:
	uv run pytest ingest/tests -q

transform-report: transform-test
	uv run python -m ingest.pipeline.transform.report --open
