"""FastAPI app. Serves HSDS v3.2.3 JSON, with routers added by each workstream."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.app.auth import require_token
from api.app.routers import conflicts, ingest, maintenance, metrics, review, services
from shared.hsds_validation import HSDS_VERSION
from shared.logging_setup import configure_logging

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.environ.get("RE_SKIP_DB_INIT") != "1":
        from shared.db import get_engine, init_db

        init_db(get_engine(), retries=15)
        log.info("database ready")
    yield


def create_app(init_database: bool = True) -> FastAPI:
    """Create the app. Every endpoint except the health check requires a bearer token."""
    configure_logging()
    app = FastAPI(
        title="The Resource Engine API",
        version="0.1.0",
        lifespan=lifespan if init_database else None,
    )
    origins = os.environ.get("RE_CORS_ORIGINS", "http://localhost:5173").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in origins if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )

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

    protected = [Depends(require_token)]
    app.include_router(services.router, dependencies=protected)
    app.include_router(ingest.router, dependencies=protected)
    app.include_router(metrics.router, dependencies=protected)
    app.include_router(maintenance.router, dependencies=protected)
    app.include_router(review.router, dependencies=protected)
    app.include_router(conflicts.router, dependencies=protected)
    return app


app = create_app()
