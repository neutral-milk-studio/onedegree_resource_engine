"""Write contracts/api/openapi.yaml from the mock API, so the two cannot drift apart."""

from __future__ import annotations

from pathlib import Path

import yaml

from contracts.mock_api import create_app

OPENAPI_PATH = Path(__file__).parent / "api" / "openapi.yaml"


def render() -> str:
    return yaml.safe_dump(create_app().openapi(), sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    OPENAPI_PATH.write_text(render())
