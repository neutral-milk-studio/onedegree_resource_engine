"""Compatibility with the AWS deploy (infra/terraform): the same app code must run
correctly under both local Docker Compose and the Terraform-provisioned environment
variables, with no code change needed to switch between them."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from api.app.auth import expected_token
from api.app.main import create_app
from shared.db import database_url, make_engine


class TestDatabaseUrl:
    def test_explicit_url_wins(self, monkeypatch):
        monkeypatch.setenv("RE_DATABASE_URL", "postgresql://a:b@c/d")
        monkeypatch.setenv("DB_HOST", "should-be-ignored")
        assert database_url() == "postgresql://a:b@c/d"

    def test_builds_from_terraform_shaped_vars(self, monkeypatch):
        monkeypatch.delenv("RE_DATABASE_URL", raising=False)
        monkeypatch.setenv("DB_HOST", "cluster.rds.amazonaws.com")
        monkeypatch.setenv("DB_USER", "resource_engine")
        monkeypatch.setenv("DB_PASSWORD", "x")
        monkeypatch.setenv("DB_NAME", "resource_engine")
        assert database_url() == (
            "postgresql://resource_engine:x@cluster.rds.amazonaws.com:5432/resource_engine"
        )

    def test_rds_managed_password_special_characters_survive_round_trip(self, monkeypatch):
        """RDS-managed passwords (manage_master_user_password=true) can contain
        symbols like @ / : + = that are meaningful in a URL; they must be escaped."""
        from urllib.parse import unquote, urlparse

        monkeypatch.delenv("RE_DATABASE_URL", raising=False)
        monkeypatch.setenv("DB_HOST", "h")
        monkeypatch.setenv("DB_USER", "u")
        monkeypatch.setenv("DB_PASSWORD", "p@ss/word:with+special=chars&more")
        monkeypatch.setenv("DB_NAME", "d")
        parsed = urlparse(database_url())
        assert unquote(parsed.password) == "p@ss/word:with+special=chars&more"

    def test_neither_configured_is_a_clear_error(self, monkeypatch):
        monkeypatch.delenv("RE_DATABASE_URL", raising=False)
        monkeypatch.delenv("DB_HOST", raising=False)
        with pytest.raises(RuntimeError, match="DB_HOST"):
            database_url()

    def test_terraform_shaped_url_actually_connects(self, monkeypatch):
        """Not just string-building: the resulting URL must work as a real DSN.

        Needs a running local Postgres on 5432 (``docker compose up -d postgres``), same
        user/db as docker-compose.yml. Skipped automatically if it isn't reachable -- CI
        doesn't run a Postgres service for this job, so the default run is unaffected.
        """
        import socket

        try:
            with socket.create_connection(("localhost", 5432), timeout=1):
                pass
        except OSError as exc:
            pytest.skip(f"Postgres not reachable at localhost:5432: {exc}")

        monkeypatch.delenv("RE_DATABASE_URL", raising=False)
        monkeypatch.setenv("DB_HOST", "localhost")
        monkeypatch.setenv("DB_USER", "resource_engine")
        monkeypatch.setenv("DB_PASSWORD", "local_dev_only")
        monkeypatch.setenv("DB_NAME", "resource_engine")
        engine = make_engine(database_url())
        with engine.connect() as conn:
            assert conn.execute(text("select 1")).scalar() == 1
        engine.dispose()


class TestAuthFallback:
    def test_falls_back_to_the_provisioned_jwt_secret(self, monkeypatch):
        """infra/terraform/data.tf provisions one real secret (random_password "jwt",
        stored in Secrets Manager) and the ECS task gets it as JWT_SECRET. Reusing it as
        the bearer token means the AWS deploy has a real, non-guessable token with
        nothing extra to configure by hand."""
        monkeypatch.delenv("RE_BEARER_TOKEN", raising=False)
        monkeypatch.setenv("JWT_SECRET", "provisioned-secret")
        assert expected_token() == "provisioned-secret"

    def test_explicit_re_bearer_token_still_wins(self, monkeypatch):
        monkeypatch.setenv("RE_BEARER_TOKEN", "explicit")
        monkeypatch.setenv("JWT_SECRET", "provisioned-secret")
        assert expected_token() == "explicit"

    def test_neither_set_rejects_every_request(self, monkeypatch):
        monkeypatch.delenv("RE_BEARER_TOKEN", raising=False)
        monkeypatch.delenv("JWT_SECRET", raising=False)
        assert expected_token() is None


class TestHealthCheck:
    @pytest.fixture
    def client(self):
        return TestClient(create_app(init_database=False))

    def test_both_health_paths_work_with_no_auth(self, client):
        """infra/terraform/compute.tf's ALB target group checks /api/health specifically
        (not /health); both must work, and neither needs a bearer token, since the load
        balancer has none."""
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/api/health").json() == {"status": "ok"}

    def test_health_paths_are_hidden_from_the_public_api_schema(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        assert "/health" not in paths and "/api/health" not in paths
