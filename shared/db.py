"""PostgreSQL access helpers shared by the HSDS store and the ops/findings schema.

Operational data lives in a separate ``ops`` schema, never mixed into the HSDS
tables. Connection settings come from the environment so secrets are never
committed.

``get_engine``/``init_db`` give SQLAlchemy access (used by the store, the pipeline and the
API). ``connection`` is the older raw psycopg helper, kept for scripts.

Spec: Requirements 11.4, 24.1.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cache

# Logical schema names. The HSDS objects live in the default/`public`-style
# schema; everything operational (findings, evidence, review state, model logs,
# user accounts) lives under `ops` so it never mixes into HSDS records.
HSDS_SCHEMA = os.environ.get("RE_HSDS_SCHEMA", "hsds")
OPS_SCHEMA = os.environ.get("RE_OPS_SCHEMA", "ops")


def database_url() -> str:
    """Return the Postgres connection URL from the environment.

    Never hard-code credentials. In local Docker Compose this points at the
    `postgres` service; in AWS it comes from Secrets Manager via the task env.
    """
    url = os.environ.get("RE_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "RE_DATABASE_URL is not set. Configure it in the environment "
            "(docker-compose provides it locally)."
        )
    return url


@contextmanager
def connection() -> Iterator[object]:
    """Yield a psycopg connection, committing on success and rolling back on error.

    psycopg is imported lazily so the contract/shared package imports cleanly in
    environments where the driver is not installed (e.g. the UI toolchain or a
    docs-only CI job).
    """
    try:
        import psycopg  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "psycopg is required for database access; add it to the service dependencies."
        ) from exc

    conn = psycopg.connect(database_url())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def sqlalchemy_url(url: str) -> str:
    """Use the psycopg 3 driver for plain ``postgresql://`` URLs."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def make_engine(url: str):
    """Create an engine. SQLite (tests) drops the hsds/ops schema names."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    url = sqlalchemy_url(url)
    if url.startswith("sqlite"):
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool if ":memory:" in url or url == "sqlite://" else None,
        )
        return engine.execution_options(schema_translate_map={HSDS_SCHEMA: None, OPS_SCHEMA: None})
    return create_engine(url, pool_pre_ping=True)


@cache
def get_engine():
    """The process-wide engine for ``RE_DATABASE_URL``."""
    return make_engine(database_url())


def init_db(engine, retries: int = 1, delay: float = 2.0) -> None:
    """Create the schemas and tables if missing. Retries while the database starts up."""
    import logging
    import time

    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    from shared.models import Base

    log = logging.getLogger(__name__)
    for attempt in range(1, retries + 1):
        try:
            with engine.begin() as conn:
                if conn.dialect.name == "postgresql":
                    conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{HSDS_SCHEMA}"'))
                    conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{OPS_SCHEMA}"'))
                Base.metadata.create_all(conn)
                _add_missing_columns(conn, Base.metadata)
            return
        except OperationalError as exc:
            if attempt == retries:
                raise
            log.warning("database not ready (attempt %s/%s): %s", attempt, retries, exc)
            time.sleep(delay)


def _add_missing_columns(conn, metadata) -> None:
    """Add model columns that an older database lacks (pilot-grade; use Alembic later)."""
    from sqlalchemy import inspect, text

    inspector = inspect(conn)
    sqlite = conn.dialect.name == "sqlite"
    for table in metadata.sorted_tables:
        schema = None if sqlite else table.schema
        existing = {c["name"] for c in inspector.get_columns(table.name, schema=schema)}
        for column in table.columns:
            if column.name in existing:
                continue
            ddl_type = column.type.compile(dialect=conn.dialect)
            name = f'"{table.name}"' if sqlite else f'"{table.schema}"."{table.name}"'
            conn.execute(text(f'ALTER TABLE {name} ADD COLUMN "{column.name}" {ddl_type}'))
