"""Thin portable SQL layer: SQLite for local runs and tests, PostgreSQL (psycopg 3) on AWS.

Rules that keep one DDL working on both engines: text primary keys, ISO-8601
text timestamps, JSON stored as TEXT, booleans as INTEGER, `?` placeholders
(rewritten to `%s` for PostgreSQL), `ON CONFLICT ... DO UPDATE` upserts.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def now_iso(precise: bool = False) -> str:
    """UTC ISO-8601. `precise` keeps microseconds so events in the same second keep their order (history)."""
    t = datetime.now(UTC)
    return (t if precise else t.replace(microsecond=0)).isoformat().replace("+00:00", "Z")


def new_id() -> str:
    return uuid.uuid4().hex


def dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


def loads(s: str | None, default: Any = None) -> Any:
    if s is None or s == "":
        return default
    return json.loads(s)


DDL = """
CREATE TABLE IF NOT EXISTS raw_payload (
  id TEXT PRIMARY KEY, source TEXT NOT NULL, native_id TEXT, fetched_at TEXT NOT NULL,
  blob_key TEXT NOT NULL, run_id TEXT);
CREATE TABLE IF NOT EXISTS import_run (
  id TEXT PRIMARY KEY, source TEXT NOT NULL, trigger TEXT NOT NULL, initiated_by TEXT,
  started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
  received INTEGER DEFAULT 0, stored INTEGER DEFAULT 0, quarantined INTEGER DEFAULT 0,
  clashes INTEGER DEFAULT 0, detail TEXT);
CREATE TABLE IF NOT EXISTS quarantine (
  id TEXT PRIMARY KEY, run_id TEXT, source TEXT NOT NULL, native_id TEXT, reason TEXT NOT NULL,
  payload TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS resource (
  id TEXT PRIMARY KEY, type TEXT NOT NULL, org_id TEXT, source TEXT, status TEXT,
  data TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_resource_type ON resource(type, deleted);
CREATE INDEX IF NOT EXISTS ix_resource_org ON resource(org_id);
CREATE TABLE IF NOT EXISTS resource_source (
  resource_id TEXT NOT NULL, source TEXT NOT NULL, native_id TEXT NOT NULL,
  fetched_at TEXT, snapshot TEXT, PRIMARY KEY (resource_id, source));
CREATE TABLE IF NOT EXISTS change_history (
  id TEXT PRIMARY KEY, resource_id TEXT NOT NULL, resource_type TEXT NOT NULL,
  field_name TEXT NOT NULL, previous_value TEXT, replacement_value TEXT,
  last_action_type TEXT NOT NULL, last_action_date TEXT NOT NULL,
  actor_type TEXT NOT NULL, updated_by TEXT NOT NULL, request_id TEXT, note TEXT);
CREATE INDEX IF NOT EXISTS ix_hist_resource ON change_history(resource_id, last_action_date);
CREATE TABLE IF NOT EXISTS change_request (
  id TEXT PRIMARY KEY, resource_id TEXT NOT NULL, resource_type TEXT NOT NULL, org_id TEXT,
  kind TEXT NOT NULL, status TEXT NOT NULL, proposed_by_type TEXT NOT NULL, proposed_by TEXT NOT NULL,
  created_at TEXT NOT NULL, decided_by TEXT, decided_at TEXT, decision_note TEXT,
  patch TEXT NOT NULL, evidence TEXT, category TEXT, title TEXT);
CREATE INDEX IF NOT EXISTS ix_cr_status ON change_request(status, kind);
CREATE TABLE IF NOT EXISTS check_result (
  resource_id TEXT PRIMARY KEY, resource_type TEXT NOT NULL, org_id TEXT, category TEXT NOT NULL,
  summary TEXT, url TEXT, checked_at TEXT NOT NULL, run_id TEXT, model TEXT, verified_marker INTEGER DEFAULT 0,
  page_hash TEXT, record_version INTEGER);
CREATE TABLE IF NOT EXISTS maintenance_run (
  id TEXT PRIMARY KEY, scope TEXT NOT NULL, trigger TEXT NOT NULL, initiated_by TEXT,
  started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, counts TEXT);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL, role TEXT NOT NULL,
  password_hash TEXT, org_id TEXT, external_id TEXT, created_at TEXT NOT NULL, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS seeker_profile (user_id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS help_request (
  id TEXT PRIMARY KEY, seeker_id TEXT NOT NULL, need_text TEXT NOT NULL, need_category TEXT,
  status TEXT NOT NULL, service_id TEXT, org_id TEXT, match_score REAL, share_consent INTEGER DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, provider_note TEXT);
CREATE INDEX IF NOT EXISTS ix_hr_org ON help_request(org_id, status);
CREATE TABLE IF NOT EXISTS chat_session (id TEXT PRIMARY KEY, user_id TEXT, created_at TEXT NOT NULL, state TEXT);
CREATE TABLE IF NOT EXISTS chat_message (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, meta TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS document (
  id TEXT PRIMARY KEY, org_id TEXT, service_id TEXT, title TEXT NOT NULL, filename TEXT NOT NULL,
  content_type TEXT, blob_key TEXT NOT NULL, visibility TEXT NOT NULL DEFAULT 'public',
  status TEXT NOT NULL DEFAULT 'pending', effective_date TEXT, uploaded_by TEXT, text_hash TEXT,
  chunk_count INTEGER DEFAULT 0, extraction TEXT, created_at TEXT NOT NULL, decided_by TEXT);
CREATE TABLE IF NOT EXISTS doc_chunk (
  id TEXT PRIMARY KEY, document_id TEXT NOT NULL, ord INTEGER NOT NULL, text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_chunk_doc ON doc_chunk(document_id);
CREATE TABLE IF NOT EXISTS api_key (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE, key_prefix TEXT NOT NULL,
  source_scope TEXT, created_by TEXT, created_at TEXT NOT NULL, revoked_at TEXT, last_used_at TEXT);
"""


class Database:
    def __init__(self, url: str):
        self.url = url
        if url.startswith("sqlite:///"):
            self.kind = "sqlite"
            self.path = url[len("sqlite:///") :]
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self._mem = None
            if self.path == ":memory:":
                self._mem = sqlite3.connect(":memory:", check_same_thread=False)
                self._mem.row_factory = sqlite3.Row
        elif url.startswith(("postgresql://", "postgres://")):
            self.kind = "postgres"
        else:
            raise ValueError(f"Unsupported DATABASE_URL: {url}")

    # -- connections ---------------------------------------------------
    @contextmanager
    def _conn(self) -> Iterator[Any]:
        if self.kind == "sqlite":
            if self._mem is not None:
                yield self._mem
                return
            c = sqlite3.connect(self.path, timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            try:
                yield c
            finally:
                c.close()
        else:  # pragma: no cover - exercised only against a real PostgreSQL
            import psycopg
            from psycopg.rows import dict_row

            c = psycopg.connect(self.url, row_factory=dict_row)
            try:
                yield c
            finally:
                c.close()

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.kind == "postgres" else sql

    def init_schema(self) -> None:
        with self.tx() as t:
            if self.kind == "postgres":  # several workers may start at once
                t.run("SELECT pg_advisory_xact_lock(727274)")
            for stmt in [s.strip() for s in DDL.split(";") if s.strip()]:
                t.run(stmt)

    @contextmanager
    def tx(self) -> Iterator[Tx]:
        with self._conn() as c:
            t = Tx(self, c)
            try:
                yield t
                c.commit()
            except Exception:
                c.rollback()
                raise

    # -- autocommit helpers ---------------------------------------------
    def q(self, sql: str, params: Iterable = ()) -> list[dict]:
        with self.tx() as t:
            return t.q(sql, params)

    def one(self, sql: str, params: Iterable = ()) -> dict | None:
        with self.tx() as t:
            return t.one(sql, params)

    def run(self, sql: str, params: Iterable = ()) -> int:
        with self.tx() as t:
            return t.run(sql, params)


class Tx:
    def __init__(self, db: Database, conn: Any):
        self.db, self.conn = db, conn

    def _exec(self, sql: str, params: Iterable):
        cur = self.conn.cursor()
        cur.execute(self.db._sql(sql), tuple(params))
        return cur

    def q(self, sql: str, params: Iterable = ()) -> list[dict]:
        cur = self._exec(sql, params)
        return [dict(r) for r in cur.fetchall()]

    def one(self, sql: str, params: Iterable = ()) -> dict | None:
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def run(self, sql: str, params: Iterable = ()) -> int:
        """Execute and return the affected row count, so callers can do compare-and-swap."""
        return self._exec(sql, params).rowcount


def database_url() -> str:
    """Return the configured PostgreSQL URL, or raise when none is set."""
    url = os.environ.get("RE_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("RE_DATABASE_URL is not set.")
    return url
