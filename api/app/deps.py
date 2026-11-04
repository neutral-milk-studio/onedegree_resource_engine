"""Shared FastAPI dependencies. Tests override ``get_store`` with a SQLite-backed store."""

from __future__ import annotations

from functools import cache

from api.app.store_pg import PgHsdsStore
from shared.db import get_engine


@cache
def _default_store() -> PgHsdsStore:
    return PgHsdsStore(get_engine())


def get_store() -> PgHsdsStore:
    return _default_store()
