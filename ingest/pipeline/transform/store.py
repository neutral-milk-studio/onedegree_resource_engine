"""Local SQLite storage in the official HSDS v3.2.3 table layout (local runs and tests).

Not the production database: loading into that is a separate workstream.

The 24 HSDS tables (columns, required columns, primary and foreign keys) are generated
from transform/schema/datapackage.json, the tabular definition published in
github.com/openreferral/specification@v3.2.3. Open Referral's own SQL files were
generated from this same file.

Everything that is not HSDS lives in separate tables (section 3.2.4):
  hsds_extension  x_resource_engine extension per record (provenance, flags)
  quarantine      objects that failed validation, with the errors
  ingest_run      counts per run
"""

import json
import sqlite3
from pathlib import Path

from .hsds import COLUMNS, TABLES

_SQL_TYPES = {
    "string": "TEXT",
    "number": "REAL",
    "date": "TEXT",
    "datetime": "TEXT",
    "time": "TEXT",
}
EXTENSION_KEY = "x_resource_engine"


def _column(field: dict, primary_key: str) -> str:
    """One column, carrying every constraint the datapackage defines: required, unique, enum."""
    name, constraints = field["name"], field.get("constraints", {})
    sql = f'  "{name}" {_SQL_TYPES[field["type"]]}'
    if constraints.get("required"):
        sql += " NOT NULL"
    if constraints.get("unique") and name != primary_key:
        sql += " UNIQUE"
    if constraints.get("enum"):
        allowed = ", ".join("'" + v.replace("'", "''") + "'" for v in constraints["enum"])
        sql += f' CHECK ("{name}" IN ({allowed}))'
    return sql


def hsds_ddl() -> str:
    """CREATE TABLE statements for every HSDS table, straight from the datapackage."""
    statements = []
    for name, schema in TABLES.items():
        lines = [_column(f, schema["primaryKey"]) for f in schema["fields"]]
        lines.append(f'  PRIMARY KEY ("{schema["primaryKey"]}")')
        for fk in schema.get("foreignKeys", []):
            ref = fk["reference"]
            lines.append(
                f'  FOREIGN KEY ("{fk["fields"]}") '
                f'REFERENCES "{ref["resource"]}" ("{ref["fields"]}")'
            )
        statements.append(f'CREATE TABLE IF NOT EXISTS "{name}" (\n' + ",\n".join(lines) + "\n);")
    return "\n".join(statements)


OPERATIONAL_DDL = """
CREATE TABLE IF NOT EXISTS hsds_extension (
    table_name TEXT NOT NULL,
    id         TEXT NOT NULL,
    source     TEXT NOT NULL,
    body       TEXT NOT NULL,
    PRIMARY KEY (table_name, id)
);

CREATE TABLE IF NOT EXISTS quarantine (
    id         INTEGER PRIMARY KEY,
    run_id     TEXT NOT NULL,
    source     TEXT NOT NULL,
    native_id  TEXT,
    kind       TEXT NOT NULL,
    object_id  TEXT,
    body       TEXT NOT NULL,
    errors     TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS quarantine_object ON quarantine(object_id);

CREATE TABLE IF NOT EXISTS ingest_run (
    id          TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    stats       TEXT NOT NULL,
    finished_at TEXT NOT NULL
);
"""


class StoreError(Exception):
    """An object the store cannot accept (unknown column, missing parent row, ...)."""


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.cx = sqlite3.connect(path)
        self.cx.execute("PRAGMA foreign_keys = ON")
        self.cx.executescript(hsds_ddl() + OPERATIONAL_DDL)

    def keep(self, kind: str, source: str, obj: dict) -> None:
        """Upsert one HSDS object into its table; its extension goes to hsds_extension."""
        if kind not in COLUMNS:
            raise StoreError(f"no HSDS table for kind {kind!r}")
        columns = COLUMNS[kind]
        unknown = set(obj) - set(columns) - {EXTENSION_KEY}
        if unknown:  # never drop data silently
            raise StoreError(f"{kind} has no column(s) {sorted(unknown)}")

        values = [obj.get(c) for c in columns]
        updates = ", ".join(f'"{c}" = excluded."{c}"' for c in columns if c != "id")
        column_list = ", ".join(f'"{c}"' for c in columns)
        try:
            self.cx.execute(
                f'INSERT INTO "{kind}" ({column_list}) VALUES ({", ".join("?" * len(columns))}) '
                f"ON CONFLICT(id) DO UPDATE SET {updates}",
                values,
            )
        except sqlite3.IntegrityError as exc:  # NOT NULL or FOREIGN KEY violation
            raise StoreError(f"{kind}: {exc}") from exc

        if EXTENSION_KEY in obj:
            self.cx.execute(
                """INSERT INTO hsds_extension (table_name, id, source, body) VALUES (?, ?, ?, ?)
                   ON CONFLICT(table_name, id)
                   DO UPDATE SET source = excluded.source, body = excluded.body""",
                (kind, obj["id"], source, json.dumps(obj[EXTENSION_KEY], sort_keys=True)),
            )
        # It passes now, so any earlier quarantine entry for it is resolved.
        self.cx.execute("DELETE FROM quarantine WHERE object_id = ?", (obj["id"],))

    def exists(self, kind: str, id: str) -> bool:
        if kind not in COLUMNS:
            return False
        return self.cx.execute(f'SELECT 1 FROM "{kind}" WHERE id = ?', (id,)).fetchone() is not None

    def quarantine(self, run_id, source, native_id, kind, object_id, body, errors) -> None:
        """Record a failure. The table holds the current failures, not a log: if the same
        object (or, for a whole-record failure, the same source record) failed before,
        that row is replaced. The run's counts stay in ingest_run."""
        if object_id:
            self.cx.execute("DELETE FROM quarantine WHERE object_id = ?", (object_id,))
        else:
            self.cx.execute(
                "DELETE FROM quarantine"
                " WHERE object_id IS NULL AND source = ? AND native_id = ? AND kind = ?",
                (source, native_id, kind),
            )
        self.cx.execute(
            """INSERT INTO quarantine
                 (run_id, source, native_id, kind, object_id, body, errors, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (run_id, source, native_id, kind, object_id, json.dumps(body), json.dumps(errors)),
        )

    def finish_run(self, run_id: str, source: str, stats: dict) -> None:
        self.cx.execute(
            "INSERT INTO ingest_run (id, source, stats, finished_at)"
            " VALUES (?, ?, ?, datetime('now'))",
            (run_id, source, json.dumps(stats)),
        )
        self.cx.commit()

    # --- read helpers (tests, inspection) ---

    def kept(self, kind: str | None = None) -> list[tuple[str, dict]]:
        """Read rows back as HSDS JSON objects (NULL columns omitted, extension merged back in)."""
        out = []
        for table in [kind] if kind else TABLES:
            columns = COLUMNS[table]
            extensions = dict(
                self.cx.execute(
                    "SELECT id, body FROM hsds_extension WHERE table_name = ?", (table,)
                )
            )
            for row in self.cx.execute(f'SELECT * FROM "{table}" ORDER BY id'):
                # REAL columns return 13.0 for 13; give whole numbers back as integers.
                obj = {
                    c: int(v) if isinstance(v, float) and v.is_integer() else v
                    for c, v in zip(columns, row, strict=True)
                    if v is not None
                }
                if obj["id"] in extensions:
                    obj[EXTENSION_KEY] = json.loads(extensions[obj["id"]])
                out.append((table, obj))
        return out

    def quarantined(self, run_id: str | None = None) -> list[dict]:
        sql, args = "SELECT native_id, kind, object_id, errors FROM quarantine", ()
        if run_id:
            sql, args = sql + " WHERE run_id = ?", (run_id,)
        return [
            {"native_id": n, "kind": k, "object_id": o, "errors": json.loads(e)}
            for n, k, o, e in self.cx.execute(sql, args)
        ]
