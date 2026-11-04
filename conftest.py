"""Shared pytest fixtures: an isolated store and raw store per test.

The store is SQLite in memory by default (CI). Set ``RE_TEST_DATABASE_URL`` to a scratch
Postgres database to run the same tests against Postgres; its tables are dropped and
recreated for every test.
"""

from __future__ import annotations

import os

import pytest

from api.app.store_pg import PgHsdsStore
from ingest.raw_store.local import LocalRawStore
from shared.db import init_db, make_engine
from shared.models import Base


@pytest.fixture
def store():
    url = os.environ.get("RE_TEST_DATABASE_URL", "sqlite://")
    engine = make_engine(url)
    if not url.startswith("sqlite"):
        init_db(engine)
        Base.metadata.drop_all(engine)
    init_db(engine)
    yield PgHsdsStore(engine)
    engine.dispose()


@pytest.fixture
def raw_store(tmp_path):
    return LocalRawStore(tmp_path / "raw")


@pytest.fixture
def loaded_store(store, raw_store):
    """The store with the ODRS, NDP and (fictional) community samples ingested."""
    from ingest.adapters.registry import get_adapter
    from ingest.pipeline.run import run_ingest

    for source in ("odrs", "ndp", "community"):
        run_ingest(get_adapter(source), store, raw_store)
    return store
