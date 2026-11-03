"""FastAPI app — STUB (filled by the Search team, tasks 26-28).

Serves HSDS v3.2.3 JSON. Mounts the root, services, and optional routers; bearer
auth + per-source scoping. Spec: Requirements 29, 30, 31, 32, 33.
"""

from __future__ import annotations

try:
    from fastapi import FastAPI
except ImportError:  # pragma: no cover - fastapi added by the API stream
    FastAPI = None  # type: ignore


def create_app():
    """Create the FastAPI app with routers mounted."""
    if FastAPI is None:
        raise RuntimeError("fastapi is not installed; add it to api/requirements.txt (task 26)")
    app = FastAPI(title="The Resource Engine API", version="0.0.0")
    # Routers (root, services, optional) are mounted by task 26.
    return app


# Uvicorn entrypoint referenced by the Dockerfile / compose.
app = None  # set by create_app() once FastAPI is available (task 26)
