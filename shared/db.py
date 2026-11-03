"""PostgreSQL access helpers shared by the HSDS store and the ops/findings schema.

Operational data lives in a separate ``ops`` schema, never mixed into the HSDS
tables. Connection settings come from the environment so secrets are never
committed.

Spec: Requirements 11.4, 24.1.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

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
