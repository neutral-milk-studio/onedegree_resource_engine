"""FastAPI app. Serves HSDS v3.2.3 JSON, with routers added by each workstream."""

from __future__ import annotations

from fastapi import Depends, FastAPI

from api.app.auth import require_token
from shared.hsds_validation import HSDS_VERSION


def create_app() -> FastAPI:
    """Create the app. Every endpoint except the health check requires a bearer token."""
    app = FastAPI(title="The Resource Engine API", version="0.1.0")

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", dependencies=[Depends(require_token)])
    def api_info() -> dict[str, str]:
        return {
            "version": HSDS_VERSION,
            "profile": "https://docs.openreferral.org/en/latest/hsds/",
            "openapi_url": "/openapi.json",
            "description": "The Resource Engine HSDS API",
        }

    return app


app = create_app()
