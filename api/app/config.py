"""Runtime settings. Everything is driven by environment variables so the same
image runs locally (SQLite, mock LLM, sample websites) and on AWS (Aurora,
Bedrock, S3, Cognito)."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    env: str = "local"
    data_dir: Path = ROOT / "var"
    database_url: str = ""  # sqlite:///path | postgresql://...
    auth_mode: str = "local"  # local | cognito
    jwt_secret: str = "dev-only-change-me"
    token_ttl_minutes: int = 480
    hsds_version: str = "3.2.3"
    default_timezone: str = "America/Los_Angeles"
    blob_backend: str = "local"  # local | s3
    s3_bucket: str = ""
    llm_backend: str = "mock"  # mock | bedrock
    bedrock_model_id: str = ""  # no default: check which models the account enables
    aws_region: str = "us-west-2"
    website_backend: str = "sample"  # sample | live
    website_samples_path: Path = ROOT / "data" / "generated" / "websites.json"  # demo only
    search_backend: str = "memory"  # memory | opensearch
    opensearch_endpoint: str = ""
    org_description_policy: str = "fallback"  # fallback | quarantine
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""
    scheduler_enabled: bool = False
    import_interval_minutes: int = 1440
    maintenance_interval_minutes: int = 1440
    maintenance_recheck_days: int = 30
    content_dir: Path = ROOT / "content"
    sources_path: Path = ROOT / "config" / "sources.json"

    def with_overrides(self, **kw) -> "Settings":
        return replace(self, **kw)


def _database_url(data_dir: Path) -> str:
    """DATABASE_URL wins. On AWS the task gets DB_HOST/DB_NAME/DB_USER/DB_PASSWORD (password from Secrets Manager)."""
    url = os.environ.get("RE_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if url:
        return url
    if os.environ.get("DB_HOST"):
        from urllib.parse import quote

        u, p = (
            quote(os.environ.get("DB_USER", "postgres"), safe=""),
            quote(os.environ.get("DB_PASSWORD", ""), safe=""),
        )
        return f"postgresql://{u}:{p}@{os.environ['DB_HOST']}:{os.environ.get('DB_PORT', '5432')}/{os.environ.get('DB_NAME', 'resource_engine')}"
    return f"sqlite:///{data_dir / 're.db'}"


def load_settings() -> Settings:
    data_dir = Path(_env("DATA_DIR", str(ROOT / "var")))
    env = _env("APP_ENV", "local")
    if (
        env != "local"
        and _env("AUTH_MODE", "local") == "local"
        and _env("JWT_SECRET", "dev-only-change-me") == "dev-only-change-me"
    ):
        raise RuntimeError("Set JWT_SECRET outside local runs.")
    return Settings(
        env=env,
        data_dir=data_dir,
        database_url=_database_url(data_dir),
        auth_mode=_env("AUTH_MODE", "local"),
        jwt_secret=_env("JWT_SECRET", "dev-only-change-me"),
        token_ttl_minutes=int(_env("TOKEN_TTL_MINUTES", "480")),
        default_timezone=_env("DEFAULT_TIMEZONE", "America/Los_Angeles"),
        blob_backend=_env("BLOB_BACKEND", "local"),
        s3_bucket=_env("S3_BUCKET", ""),
        llm_backend=_env("LLM_BACKEND", "mock"),
        bedrock_model_id=_env("BEDROCK_MODEL_ID", ""),
        aws_region=_env("AWS_REGION", "us-west-2"),
        website_backend=_env("RE_WEBSITE_SOURCE", _env("WEBSITE_BACKEND", "sample")),
        website_samples_path=Path(
            _env("WEBSITE_SAMPLES_PATH", str(ROOT / "data" / "generated" / "websites.json"))
        ),
        search_backend=_env("SEARCH_BACKEND", "memory"),
        opensearch_endpoint=_env("OPENSEARCH_ENDPOINT", ""),
        org_description_policy=_env("ORG_DESCRIPTION_POLICY", "fallback"),
        cognito_user_pool_id=_env("COGNITO_USER_POOL_ID", ""),
        cognito_client_id=_env("COGNITO_CLIENT_ID", ""),
        scheduler_enabled=_env("SCHEDULER_ENABLED", "false").lower() == "true",
        import_interval_minutes=int(_env("IMPORT_INTERVAL_MINUTES", "1440")),
        maintenance_interval_minutes=int(_env("MAINTENANCE_INTERVAL_MINUTES", "1440")),
        maintenance_recheck_days=int(_env("RE_CHECK_INTERVAL_DAYS", "30")),
        sources_path=Path(_env("SOURCES_CONFIG", str(ROOT / "config" / "sources.json"))),
    )
