"""Visual report of a transform run, as one self-contained HTML page.

    python -m ingest.pipeline.transform.report --open             # both sources, sample pages
    python -m ingest.pipeline.transform.report ndp page.json --open  # one source, your files

Runs the real pipeline (ingest.pipeline.run.run_ingest) into a throwaway in-memory store,
then shows:
  - each pipeline stage with its counts (fetched -> mapped -> validated -> parents -> stored)
  - stored and quarantined objects per HSDS table, and every quarantine reason, grouped
  - a record explorer: the raw source record next to the HSDS objects it became,
    drawn as a parent -> child tree, each object as it was stored
"""

import argparse
import json
import re
import webbrowser
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from ingest.pipeline.run import run_ingest

from .adapter import MapperAdapter, flatten
from .hsds import parents
from .run import MAPPERS, sample_pages
from .store import Store

TEMPLATE = Path(__file__).parent / "report.html"
PARENT_ERROR = "(root): parent "
STORE_ERRORS = ("NOT NULL", "FOREIGN KEY", "has no column", "no HSDS table")
# "number: 'my.commute.org' contains no digits" -> hide the value, keep the field name.
# "(root): 'city' is a required property" is left alone: there the quoted text is the field.
_FIELD_VALUE = re.compile(r"^(?!\(root\))([^:]+: )'[^']*'")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _label(kind: str, obj: dict) -> str:
    """A short human name for an object (or raw record) in the tree."""
    for key in ("name", "title", "number", "address_1", "term", "language", "description"):
        if obj.get(key):
            return str(obj[key])[:90]
    if kind == "schedule":
        days = obj.get("byday") or obj.get("freq") or ""
        hours = "-".join(filter(None, [obj.get("opens_at"), obj.get("closes_at")]))
        return f"{days} {hours}".strip() or "schedule"
    return kind.replace("_", " ")


def _stage(errors: list[str]) -> str:
    """Which pipeline stage quarantined an object, from its error messages."""
    if all(e.startswith(PARENT_ERROR) for e in errors):
        return "parents"
    if any(marker in e for e in errors for marker in STORE_ERRORS):
        return "store"
    return "validation"


def _reason(error: str) -> str:
    """Group similar errors: drop the values and ids that differ between objects."""
    return _UUID.sub("<id>", _FIELD_VALUE.sub(r"\1'…'", error))


def _tree(pairs: list[tuple[str, dict]], stored: dict, failed: dict) -> list[dict]:
    """Nest one record's objects under their first parent from the same record."""
    nodes: dict[str, dict] = {}
    placed: list[tuple[str, dict, dict]] = []
    for kind, obj in pairs:
        oid = obj.get("id") or f"no-id-{len(nodes)}"
        if oid in nodes:
            continue
        nodes[oid] = node = {
            "kind": kind,
            "id": oid,
            "label": _label(kind, obj),
            "status": "stored" if oid in stored else "quarantined",
            "errors": failed.get(oid, []),
            "obj": stored.get(oid, obj),  # the stored row read back, else what the mapper made
            "children": [],
        }
        placed.append((kind, obj, node))
    roots = []
    for kind, obj, node in placed:
        parent = next(
            (pid for _, pid in parents(kind, obj) if pid in nodes and pid != node["id"]), None
        )
        (nodes[parent]["children"] if parent else roots).append(node)
    return roots


def build(mapper, pages: list[Path]) -> dict:
    adapter = MapperAdapter(mapper, pages)
    store = Store()
    counts = run_ingest(adapter, store)
    (stats_json,) = store.cx.execute("SELECT stats FROM ingest_run").fetchone()
    stats = json.loads(stats_json)

    stored = {obj["id"]: obj for _, obj in store.kept()}
    quarantined = store.quarantined()
    failed = {q["object_id"]: q["errors"] for q in quarantined if q["object_id"]}
    record_failed = {q["native_id"]: q["errors"] for q in quarantined if q["kind"] == "record"}

    stages = Counter(_stage(q["errors"]) for q in quarantined if q["kind"] != "record")
    reasons: dict[tuple[str, str], set] = defaultdict(set)
    for q in quarantined:
        for error in q["errors"]:
            reasons[(q["kind"], _reason(error))].add(q["native_id"])

    kinds_stored = Counter(kind for kind, _ in store.kept())
    kinds_quarantined = Counter(q["kind"] for q in quarantined)

    records = []
    for record in adapter.fetch():
        native_id = str(record.get("id"))
        try:
            pairs = flatten(adapter.map(record))
        except Exception:
            pairs = []
        tree = _tree(pairs, stored, failed)
        problems = sum(1 for _, o in pairs if o.get("id") in failed)
        records.append(
            {
                "native_id": native_id,
                "label": _label("record", record),
                "failed": record_failed.get(native_id),
                "objects": len(pairs),
                "problems": problems,
                "raw": record,
                "tree": tree,
            }
        )

    return {
        "source": mapper.SOURCE,
        "files": [str(p) for p in pages],
        "counts": vars(counts),
        "stats": stats,
        "stages": {
            "records": stats.get("records", 0),
            "map_failed": stats.get("records_quarantined", 0),
            "objects": stats.get("objects", 0),
            "validation": stages["validation"],
            "parents": stages["parents"],
            "store": stages["store"],
            "kept": stats.get("kept", 0),
            "quarantined": stats.get("quarantined", 0),
            "unique_rows": len(stored),
        },
        "kinds": [
            {"kind": k, "stored": kinds_stored[k], "quarantined": kinds_quarantined[k]}
            for k in sorted(set(kinds_stored) | set(kinds_quarantined))
        ],
        "reasons": sorted(
            (
                {"kind": kind, "reason": reason, "records": sorted(ids)}
                for (kind, reason), ids in reasons.items()
            ),
            key=lambda r: -len(r["records"]),
        ),
        "records": records,
    }


def render(sources: list[dict]) -> str:
    data = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "sources": sources}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.read_text().replace("__DATA__", payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Write an HTML report of a transform run.")
    parser.add_argument(
        "source",
        nargs="?",
        choices=sorted(MAPPERS),
        help="one source only (default: every source, on its sample pages)",
    )
    parser.add_argument("pages", nargs="*", type=Path, help="page files for that source")
    parser.add_argument("--out", type=Path, default=Path("transform-report.html"))
    parser.add_argument("--open", action="store_true", help="open the report in a browser")
    args = parser.parse_args()
    if args.pages and not args.source:
        parser.error("name the source the page files belong to")

    sources = [args.source] if args.source else sorted(MAPPERS)
    built = []
    for source in sources:
        pages = args.pages or sample_pages(source)
        if not pages:
            print(f"skipping {source}: no sample pages found")
            continue
        report = build(MAPPERS[source], pages)
        c = report["counts"]
        print(
            f"{source}: received {c['received']} = stored {c['stored']}"
            f" + quarantined {c['quarantined']}"
        )
        built.append(report)

    args.out.write_text(render(built))
    print(f"report: {args.out.resolve()}")
    if args.open:
        webbrowser.open(args.out.resolve().as_uri())


if __name__ == "__main__":
    main()
