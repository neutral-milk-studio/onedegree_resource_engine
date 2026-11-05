"""Bearer token auth, source scoping, and reviewer identity.

Two ways in, tried in order:

1. A Cognito ID token (api/app/cognito_auth.py), when the AWS deploy's user pool is
   configured -- the caller's real, per-specialist identity (their email), so an edit or
   audit entry traces back to the person who made it, not a secret anyone could have used.
2. The shared ``RE_BEARER_TOKEN`` (or, on AWS, ``JWT_SECRET``), as before -- local
   development, and any deploy that hasn't set up Cognito sign-in yet.
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.app.cognito_auth import CognitoTokenError, verify_id_token

log = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)


def expected_token() -> str | None:
    """``RE_BEARER_TOKEN`` if set; otherwise the AWS deploy's provisioned app secret
    (``JWT_SECRET``, from Secrets Manager via infra/terraform/data.tf's ``random_password``),
    so a deployed instance has a real token without a second value to configure by hand."""
    return os.environ.get("RE_BEARER_TOKEN") or os.environ.get("JWT_SECRET")


def require_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    """Return the caller's identity (a Cognito email, or the shared token's dev user),
    or respond with 401."""
    token = credentials.credentials if credentials else ""
    if token:
        try:
            return verify_id_token(token)
        except CognitoTokenError as exc:
            log.debug("not a valid Cognito ID token, trying the shared token", exc_info=exc)

    expected = expected_token()
    if expected and token and secrets.compare_digest(token, expected):
        return os.environ.get("RE_DEV_USER", "dev-reviewer")
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Missing or invalid bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )
