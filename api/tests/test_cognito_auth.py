"""Cognito ID token verification: real RSA signing and real JWT verification
(api/app/cognito_auth.py) -- only the network JWKS fetch is stubbed, the same boundary
LocalStack/StubBedrock stub elsewhere in this project (a real AWS network call needs real
credentials no sandbox has; the signature/issuer/audience/expiry checks are exercised for
real)."""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWK

from api.app.cognito_auth import CognitoTokenError, verify_id_token

USER_POOL_ID = "us-west-2_testpool"
REGION = "us-west-2"
CLIENT_ID = "test-client-id"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}"


@pytest.fixture
def keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _token(private_key, **claim_overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "token_use": "id",
        "email": "specialist@example.org",
        "iat": now,
        "exp": now + 3600,
        **claim_overrides,
    }
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})


@pytest.fixture
def cognito_env(monkeypatch, keypair, request):
    private_key, public_key = keypair
    monkeypatch.setenv("COGNITO_USER_POOL_ID", USER_POOL_ID)
    monkeypatch.setenv("COGNITO_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("AWS_REGION", REGION)

    import api.app.cognito_auth as cognito_auth

    class StubJWKClient:
        def get_signing_key_from_jwt(self, token: str) -> PyJWK:
            return PyJWK.from_json(
                __import__("json").dumps(
                    {
                        "kty": "RSA",
                        "n": jwt.utils.to_base64url_uint(public_key.public_numbers().n).decode(),
                        "e": jwt.utils.to_base64url_uint(public_key.public_numbers().e).decode(),
                    }
                ),
                algorithm="RS256",
            )

    cognito_auth._jwk_client.cache_clear()
    monkeypatch.setattr(cognito_auth, "_jwk_client", lambda issuer: StubJWKClient())
    return private_key


def test_verifies_a_real_signed_token_and_returns_the_email(cognito_env):
    private_key = cognito_env
    assert verify_id_token(_token(private_key)) == "specialist@example.org"


def test_rejects_a_token_signed_by_a_different_key(cognito_env):
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(CognitoTokenError):
        verify_id_token(_token(other_key))


def test_rejects_an_expired_token(cognito_env):
    private_key = cognito_env
    expired = _token(private_key, iat=int(time.time()) - 7200, exp=int(time.time()) - 3600)
    with pytest.raises(CognitoTokenError):
        verify_id_token(expired)


def test_rejects_the_wrong_audience(cognito_env):
    private_key = cognito_env
    with pytest.raises(CognitoTokenError):
        verify_id_token(_token(private_key, aud="someone-elses-client"))


def test_rejects_an_access_token_not_an_id_token(cognito_env):
    private_key = cognito_env
    with pytest.raises(CognitoTokenError):
        verify_id_token(_token(private_key, token_use="access"))


def test_rejects_a_token_with_no_email_claim(cognito_env):
    private_key = cognito_env
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": CLIENT_ID, "token_use": "id", "iat": now, "exp": now + 3600}
    token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})
    with pytest.raises(CognitoTokenError):
        verify_id_token(token)


def test_raises_immediately_when_cognito_is_not_configured(monkeypatch):
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.delenv("COGNITO_CLIENT_ID", raising=False)
    with pytest.raises(CognitoTokenError, match="not configured"):
        verify_id_token("whatever")


def test_an_edit_made_with_a_cognito_token_is_attributed_to_the_real_specialist(
    cognito_env, store, raw_store, monkeypatch
):
    """End to end: a Cognito-signed-in request's audit entry names the real person, not
    a shared dev user -- the accountability gap this closes."""
    from fastapi.testclient import TestClient

    from api.app.deps import get_store
    from api.app.main import create_app

    monkeypatch.setenv("RE_RAW_DIR", str(raw_store.root))
    monkeypatch.delenv("RE_BEARER_TOKEN", raising=False)  # Cognito alone must be enough
    app = create_app(init_database=False)
    app.dependency_overrides[get_store] = lambda: store
    client = TestClient(app)

    token = _token(cognito_env)
    response = client.post("/ingest/odrs", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200

    audit = client.get("/audit", headers={"Authorization": f"Bearer {token}"}).json()
    assert audit[0]["actor"] == "specialist@example.org"


def test_a_bad_token_falls_back_to_the_shared_token_not_a_500(
    cognito_env, store, raw_store, monkeypatch
):
    from fastapi.testclient import TestClient

    from api.app.deps import get_store
    from api.app.main import create_app

    monkeypatch.setenv("RE_BEARER_TOKEN", "shared-secret")
    monkeypatch.setenv("RE_RAW_DIR", str(raw_store.root))
    app = create_app(init_database=False)
    app.dependency_overrides[get_store] = lambda: store
    client = TestClient(app)

    garbage = client.get("/health", headers={"Authorization": "Bearer not-a-jwt-at-all"})
    assert garbage.status_code == 200  # /health needs no auth; just proves no 500 on garbage

    rejected = client.get("/services", headers={"Authorization": "Bearer not-a-jwt-at-all"})
    assert rejected.status_code == 401

    accepted = client.get("/services", headers={"Authorization": "Bearer shared-secret"})
    assert accepted.status_code == 200
