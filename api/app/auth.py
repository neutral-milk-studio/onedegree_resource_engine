"""Bearer token auth, source scoping, and reviewer identity.

Local development uses one token from ``RE_BEARER_TOKEN``. Replace this with real
accounts and per-source keys before anything beyond a demo.
"""

from __future__ import annotations

import os
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)


def require_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    """Return the caller's identity, or respond with 401."""
    expected = os.environ.get("RE_BEARER_TOKEN")
    token = credentials.credentials if credentials else ""
    if not expected or not secrets.compare_digest(token, expected):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing or invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return os.environ.get("RE_DEV_USER", "dev-reviewer")
