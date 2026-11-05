"""Cognito ID token verification: real per-specialist identity, when the AWS deploy's
Cognito user pool is configured, instead of one shared bearer token.

Verifies the token's signature against the user pool's public JWKS (fetched once per
process and cached), its issuer and audience, and that it hasn't expired, then returns
the signed-in person's email -- every edit and audit entry traces back to them, not a
secret anyone with the token could have used.

Costs nothing when Cognito isn't configured (the common case today): no network call is
attempted, ``verify_id_token`` just raises immediately, and ``require_token`` falls back
to the existing shared-token check (api/app/auth.py).
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any

import jwt
from jwt import PyJWKClient

log = logging.getLogger(__name__)


class CognitoTokenError(Exception):
    pass


def _issuer() -> str | None:
    pool_id = os.environ.get("COGNITO_USER_POOL_ID")
    if not pool_id:
        return None
    region = os.environ.get("AWS_REGION", "us-west-2")
    return f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"


@lru_cache(maxsize=1)
def _jwk_client(issuer: str) -> PyJWKClient:
    """Cached per issuer (effectively once per process): fetches and caches the user
    pool's public signing keys on first use."""
    return PyJWKClient(f"{issuer}/.well-known/jwks.json")


def verify_id_token(token: str) -> str:
    """Return the signed-in person's email, or raise CognitoTokenError."""
    issuer = _issuer()
    client_id = os.environ.get("COGNITO_CLIENT_ID")
    if not issuer or not client_id:
        raise CognitoTokenError("Cognito is not configured")
    try:
        signing_key = _jwk_client(issuer).get_signing_key_from_jwt(token)
        claims: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=issuer,
            options={"require": ["exp", "iat"]},
        )
    except jwt.PyJWTError as exc:
        raise CognitoTokenError(str(exc)) from exc
    if claims.get("token_use") != "id":
        raise CognitoTokenError("not an ID token")
    email = claims.get("email") or claims.get("cognito:username")
    if not email:
        raise CognitoTokenError("token has no email claim")
    return str(email)
