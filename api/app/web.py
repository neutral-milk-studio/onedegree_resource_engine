"""What the routers share: the running container, errors, and authentication."""

from __future__ import annotations

from contextvars import ContextVar

from fastapi import Request

from api.app.container import Container
from api.app.services.users import AuthError, Principal

current_container: ContextVar[Container] = ContextVar("current_container")


def container() -> Container:
    return current_container.get()


class ApiError(Exception):
    """An error response. The UI reads ``error`` and any extra fields."""

    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


def current_principal(request: Request) -> Principal | None:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    try:
        return container().auth.verify(header[7:])
    except AuthError:
        return None


def auth_required(*roles: str):
    """A dependency that returns the signed-in person, or responds with 401 or 403."""

    def dependency(request: Request) -> Principal:
        principal = current_principal(request)
        if not principal:
            raise ApiError(401, "Log in to continue.")
        if roles and principal.role not in roles:
            raise ApiError(403, "You do not have access to that.")
        return principal

    return dependency


async def json_body(request: Request) -> dict:
    """The JSON request body, or an empty object when there is none."""
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
