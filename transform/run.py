"""Transform runner: raw pages -> map -> validate -> keep or quarantine.

Usage:
    python -m transform.run odrs                       # uses data/samples/ODRS/*.json
    python -m transform.run ndp                        # uses data/samples/NDP/*.json
    python -m transform.run odrs path/to/page-1.json path/to/page-2.json --db resource_engine.db
"""

import argparse
import json
import uuid
from collections import Counter
from pathlib import Path
from types import ModuleType

from . import ndp, odrs
from .hsds import parents, validate
from .store import Store, StoreError

ADAPTERS: dict[str, ModuleType] = {odrs.SOURCE: odrs, ndp.SOURCE: ndp}
SAMPLES_DIR = Path(__file__).parents[1] / "data" / "samples"

def split(objects: list[tuple[str, dict]], exists=lambda kind, id: False):
    """Validate each object and check its parents; return (good, bad).

    A child is bad if its parent failed (the failure cascades down) or if its parent is
    neither in this batch nor already stored. Checking this here keeps the output valid
    HSDS whatever database it ends up in, including ones that don't enforce foreign keys.
    """
    bad: dict[str, tuple[str, dict, list[str]]] = {}
    for kind, obj in objects:
        if errors := validate(kind, obj):
            bad[obj.get("id") or f"no-id-{len(bad)}"] = (kind, obj, errors)
    in_batch = {obj.get("id") for _, obj in objects}

    changed = True
    while changed:
        changed = False
        for kind, obj in objects:
            if obj.get("id") in bad:
                continue
            for parent_kind, parent_id in parents(kind, obj):
                if parent_id in bad:
                    reason = f"(root): parent {parent_kind} {parent_id} was quarantined"
                elif parent_id not in in_batch and not exists(parent_kind, parent_id):
                    reason = f"(root): parent {parent_kind} {parent_id} does not exist"
                else:
                    continue
                bad[obj["id"]] = (kind, obj, [reason])
                changed = True
                break

    good = [(k, o) for k, o in objects if o.get("id") not in bad]
    return good, list(bad.values())


def run(adapter: ModuleType, pages: list[dict], store: Store, run_id: str | None = None) -> dict:
    run_id = run_id or str(uuid.uuid4())
    source = adapter.SOURCE
    stats = Counter()

    for page in pages:
        for record in adapter.records(page):
            stats["records"] += 1
            native_id = adapter.native_id(record)
            try:
                objects = adapter.map(record)
            except Exception as exc:  # unexpected shape or mapper bug: quarantine the whole record
                store.quarantine(run_id, source, native_id, "record", None, record, [repr(exc)])
                stats["records_quarantined"] += 1
                continue

            good, bad = split(objects, store.exists)
            kept = 0
            for kind, obj in good:  # mappers emit parents before children
                try:
                    store.keep(kind, source, obj)
                except StoreError as exc:  # e.g. a required column or parent row the schema check can't see
                    bad.append((kind, obj, [str(exc)]))
                    continue
                kept += 1
                stats[f"kept.{kind}"] += 1
            for kind, obj, errors in bad:
                store.quarantine(run_id, source, native_id, kind, obj.get("id"), obj, errors)

            stats["objects"] += len(objects)
            stats["kept"] += kept
            stats["quarantined"] += len(bad)

    # R1.7: every object is accounted for.
    assert stats["objects"] == stats["kept"] + stats["quarantined"], stats
    result = {"run_id": run_id, "source": source, **dict(sorted(stats.items()))}
    store.finish_run(run_id, source, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Transform raw source pages into HSDS.")
    parser.add_argument("source", choices=sorted(ADAPTERS))
    parser.add_argument("pages", nargs="*", type=Path,
                        help="raw API page files (JSON); defaults to the source's sample files")
    parser.add_argument("--db", default="resource_engine.db")
    args = parser.parse_args()

    paths = args.pages or sorted((SAMPLES_DIR / args.source.upper()).glob("*.json"))
    if not paths:
        parser.error(f"no page files given and no samples found in {SAMPLES_DIR / args.source.upper()}")
    print(f"Transforming {len(paths)} file(s): {', '.join(str(p) for p in paths)}")

    store = Store(args.db)
    pages = [json.loads(p.read_text()) for p in paths]
    result = run(ADAPTERS[args.source], pages, store)
    print(json.dumps(result, indent=2))
    for q in store.quarantined(result["run_id"]):
        print(f"QUARANTINED {q['kind']} (record {q['native_id']}): {'; '.join(q['errors'])}")


if __name__ == "__main__":
    main()
