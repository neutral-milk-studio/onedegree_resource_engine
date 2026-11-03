"""Load the seeded HSDS dataset into the HSDS store.

Gives every team a shared, schema-valid starting dataset. The AI Maintenance
team runs against this so it is never blocked on Data Ingest.

Spec: Requirement 27.1.

Usage:
    python -m seed.load_seed              # load into the configured store
    python -m seed.load_seed --dry-run    # parse + report counts only
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DATASET = Path(__file__).resolve().parent / "hsds_dataset.json"


def load_dataset() -> dict[str, Any]:
    """Parse the seed dataset JSON."""
    return json.loads(DATASET.read_text(encoding="utf-8"))


def _records(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten organizations + services into upsertable records."""
    orgs = dataset.get("organizations", [])
    services = dataset.get("services", [])
    return [*orgs, *services]


def load_into_store(store: Any, dataset: dict[str, Any] | None = None) -> int:
    """Upsert every seed record into the given HSDS store. Returns the count.

    ``store`` is any object satisfying the HsdsStore contract. Each record's
    ``x_resource_engine.source`` carries the source system and native record id
    used for the deterministic id.
    """
    dataset = dataset or load_dataset()
    count = 0
    for record in _records(dataset):
        src = record.get("x_resource_engine", {}).get("source", {})
        source = src.get("system", "seed")
        native_id = src.get("record_id", record["id"])
        # Group the record under its HSDS object type for upsert.
        is_org = "description" in record and "status" not in record
        object_type = "organization" if is_org else "service"
        store.upsert_record({object_type: [record]}, source=source, native_id=native_id)
        count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Load the seeded HSDS dataset.")
    parser.add_argument("--dry-run", action="store_true", help="parse and report only")
    args = parser.parse_args()

    dataset = load_dataset()
    records = _records(dataset)
    if args.dry_run:
        orgs = len(dataset.get("organizations", []))
        svcs = len(dataset.get("services", []))
        print(
            f"seed dataset OK: {orgs} organizations, {svcs} services, {len(records)} records total"
        )
        return

    from shared.db import connection  # noqa: F401  (import check only here)

    raise SystemExit(
        "Connect a concrete HsdsStore implementation and call load_into_store(store). "
        "Run with --dry-run to validate the dataset without a database."
    )


if __name__ == "__main__":
    main()
