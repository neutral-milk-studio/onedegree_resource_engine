"""The store uses the official HSDS v3.2.3 table layout and enforces its keys."""

import pytest

from transform.hsds import hsds_id
from transform.store import COLUMNS, TABLES, Store, StoreError

ORG = {"id": hsds_id("t", "organization", 1), "name": "Org", "description": "D"}
SVC = {"id": hsds_id("t", "service", 1), "organization_id": ORG["id"], "name": "S", "status": "active"}


def table_columns(store, table):
    return [row[1] for row in store.cx.execute(f'PRAGMA table_info("{table}")')]


def test_all_24_hsds_tables_exist_with_official_columns():
    store = Store()
    assert len(TABLES) == 24
    for table, columns in COLUMNS.items():
        assert table_columns(store, table) == columns


def test_required_and_foreign_keys_come_from_the_datapackage():
    store = Store()
    service = {row[1]: row[3] for row in store.cx.execute('PRAGMA table_info("service")')}
    assert service["name"] == 1 and service["status"] == 1 and service["organization_id"] == 1  # NOT NULL
    fks = {(row[3], row[2]) for row in store.cx.execute('PRAGMA foreign_key_list("phone")')}
    assert {("service_id", "service"), ("location_id", "location"), ("organization_id", "organization")} <= fks


def test_round_trip_with_extension():
    store = Store()
    org = {**ORG, "x_resource_engine": {"source": {"system": "t", "record_id": "1"}}}
    store.keep("organization", "t", org)
    assert store.kept("organization") == [("organization", org)]
    # The extension is not an HSDS column: it lives in hsds_extension.
    assert "x_resource_engine" not in table_columns(store, "organization")


def test_upsert_updates_in_place():
    store = Store()
    store.keep("organization", "t", ORG)
    store.keep("organization", "t", {**ORG, "name": "Renamed"})
    assert [o["name"] for _, o in store.kept("organization")] == ["Renamed"]


def test_missing_parent_row_is_rejected():
    with pytest.raises(StoreError, match="FOREIGN KEY"):
        Store().keep("service", "t", SVC)  # organization not stored


def test_missing_required_column_is_rejected():
    store = Store()
    store.keep("organization", "t", ORG)
    with pytest.raises(StoreError, match="NOT NULL"):
        store.keep("service", "t", {k: v for k, v in SVC.items() if k != "organization_id"})


def test_unknown_property_is_rejected_not_dropped():
    store = Store()
    with pytest.raises(StoreError, match="no column"):
        store.keep("organization", "t", {**ORG, "phones": []})


def test_unique_and_enum_constraints_from_the_datapackage():
    store = Store()
    ddl = {name: sql for name, sql in store.cx.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")}
    assert '"code" TEXT UNIQUE' in ddl["taxonomy_term"]
    assert "CHECK (\"status\" IN ('active', 'inactive', 'defunct', 'temporarily closed'))" in ddl["service"]
    store.keep("organization", "t", ORG)
    with pytest.raises(StoreError, match="CHECK"):
        store.keep("service", "t", {**SVC, "status": "open"})


def test_rerunning_does_not_duplicate_quarantine_rows():
    store = Store()
    for run_id in ("r1", "r2"):
        store.quarantine(run_id, "t", "1", "organization", ORG["id"], ORG, ["boom"])
        store.quarantine(run_id, "t", "9", "record", None, {"id": 9}, ["bad shape"])
    rows = store.quarantined()
    assert len(rows) == 2
    assert store.quarantined("r2") == rows  # the latest run's failures replace the earlier ones
