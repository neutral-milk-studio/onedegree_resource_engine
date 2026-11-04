"""FastAPI app: the UI's API under /api, the HSDS API under /hsds, and the UI itself."""

from __future__ import annotations

from pathlib import Path
from threading import Lock

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException

from api.app.config import ROOT, Settings
from api.app.container import Container, build_container
from api.app.routers.app import router as app_router
from api.app.routers.hsds import router as hsds_router
from api.app.services.chat import ConsentRequired
from api.app.services.proposals import Stale
from api.app.services.store import Conflict, ValidationFailed
from api.app.services.users import AuthError
from api.app.web import ApiError, current_container
from shared.hsds_validation import HSDS_VERSION

UI_DIR = ROOT / "ui"


def _error(status: int, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": message, **extra}, status_code=status)


def _ui_files(ui_dir: Path) -> set[str]:
    """Only the app's own files are served, never tests, packages or anything else in the folder."""
    return {
        path.name
        for path in ui_dir.glob("*")
        if path.suffix in (".html", ".js", ".css") and not path.name.endswith(".test.js")
    }


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    app = FastAPI(title="The Resource Engine API", version=HSDS_VERSION)
    lock, built = Lock(), [container]

    def get_container() -> Container:
        with lock:  # built on the first request, so importing the app touches no database
            if built[0] is None:
                built[0] = build_container(settings)
            return built[0]

    @app.middleware("http")
    async def provide_container(request: Request, call_next):
        current_container.set(get_container())
        return await call_next(request)

    @app.exception_handler(ApiError)
    async def _api_error(_request, exc: ApiError):
        return _error(exc.status, exc.message, **exc.extra)

    @app.exception_handler(ValidationFailed)
    async def _invalid(_request, exc: ValidationFailed):
        return _error(422, "This change would produce invalid HSDS data.", details=exc.errors)

    @app.exception_handler(RequestValidationError)
    async def _bad_request(_request, exc: RequestValidationError):
        details = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
        return _error(422, "The request is not valid.", details=details)

    @app.exception_handler(AuthError)
    async def _auth(_request, exc: AuthError):
        return _error(401, str(exc))

    @app.exception_handler(PermissionError)
    async def _forbidden(_request, exc: PermissionError):
        return _error(403, str(exc) or "You do not have access to that.")

    @app.exception_handler(KeyError)
    async def _not_found(_request, _exc: KeyError):
        return _error(404, "Not found.")

    @app.exception_handler(Stale)
    async def _stale(_request, exc: Stale):
        return _error(409, str(exc), stale_fields=exc.fields)

    @app.exception_handler(Conflict)
    async def _conflict(_request, exc: Conflict):
        return _error(409, str(exc))

    @app.exception_handler(ConsentRequired)
    async def _consent(_request, _exc: ConsentRequired):
        message = "Share your details with providers before sending a request."
        return _error(409, message, code="consent_required")

    @app.exception_handler(HTTPException)
    async def _http(_request, exc: HTTPException):
        return _error(exc.status_code, str(exc.detail))

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(app_router, prefix="/api", tags=["app"])
    app.include_router(hsds_router, prefix="/hsds", tags=["hsds"])

    ui_files = _ui_files(UI_DIR)

    @app.get("/{path:path}", include_in_schema=False)
    def ui(path: str):
        if path.startswith(("api/", "hsds/")):
            raise ApiError(404, "Not found.")
        return FileResponse(UI_DIR / (path if path in ui_files else "index.html"))

    return app


app = create_app()
