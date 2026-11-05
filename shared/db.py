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

    Never hard-code credentials. In local Docker Compose this is ``RE_DATABASE_URL``
    directly, pointing at the `postgres` service. On the AWS deploy
    (``infra/terraform``), the ECS task gets the Aurora endpoint and credentials as
    separate variables instead (``DB_HOST``/``DB_NAME``/``DB_USER``/``DB_PASSWORD``,
    the last two injected from Secrets Manager) — this assembles the same URL shape
    from those so nothing else in the app needs to know which environment it's in.
    """
    url = os.environ.get("RE_DATABASE_URL")
    if url:
        return url
    host = os.environ.get("DB_HOST")
    if host:
        from urllib.parse import quote

        user = quote(os.environ.get("DB_USER", ""), safe="")
        password = quote(os.environ.get("DB_PASSWORD", ""), safe="")
        name = os.environ.get("DB_NAME", "resource_engine")
        port = os.environ.get("DB_PORT", "5432")
        return f"postgresql://{user}:{password}@{host}:{port}/{name}"
    raise RuntimeError(
        "No database configured: set RE_DATABASE_URL (local), or DB_HOST (+ DB_USER / "
        "DB_PASSWORD / DB_NAME) for the AWS deploy."
    )


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
                if conn.dialect.name == "postgresql":
                    # Full-text index for search (search/query/search.py).
                    conn.execute(text(
                        "CREATE INDEX IF NOT EXISTS services_search_fts "
                        f'ON "{HSDS_SCHEMA}".services '
                        "USING gin (to_tsvector('english', coalesce(search_text, '')))"
                    ))  # fmt: skip
            _backfill_search_text(engine)
            _backfill_embeddings(engine)
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


def _backfill_search_text(engine) -> None:
    """Fill search text for rows saved before the column existed (one-time, cheap)."""
    from sqlalchemy import select, update

    from search.indexer.build_index import search_text
    from shared.models import Service

    with engine.begin() as conn:
        rows = conn.execute(
            select(Service.id, Service.document).where(Service.search_text.is_(None))
        ).all()
        for service_id, document in rows:
            conn.execute(
                update(Service)
                .where(Service.id == service_id)
                .values(search_text=search_text(document))
            )


def _backfill_embeddings(engine) -> None:
    """Fill embeddings for rows saved before the column existed, or by a stale model.

    Normal ingest already embeds every record it creates or changes
    (``api/app/store_pg.py``); this only catches rows from before that, or a model switch.
    """
    from sqlalchemy import select, update

    from search.embeddings.embed import get_embedder
    from search.indexer.build_index import search_text
    from shared.models import Service

    embedder = get_embedder()
    with engine.begin() as conn:
        rows = conn.execute(
            select(
                Service.id,
                Service.document,
                Service.name,
                Service.embedding,
                Service.embedding_model_id,
            )  # fmt: skip
        ).all()
        for service_id, document, name, embedding, model_id in rows:
            if embedding is not None and model_id == embedder.model_id:
                continue
            vector = embedder.embed(search_text(document) or name)
            conn.execute(
                update(Service)
                .where(Service.id == service_id)
                .values(embedding=vector, embedding_model_id=embedder.model_id)
            )
