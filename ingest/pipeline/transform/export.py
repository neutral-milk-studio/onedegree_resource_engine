"""Export the stored HSDS objects as JSON files, one file per object.

    python -m ingest.pipeline.transform.export              # resource_engine.db -> export/
    python -m ingest.pipeline.transform.export --db other.db --out somewhere/

Layout: export/<kind>/<id>.json, e.g. export/service/e3ba6d13-....json
Each file is one HSDS object exactly as the transform produced it, so it can be
read directly or checked against Open Referral's schema with any JSON Schema tool.
"""

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path

from .store import Store


def export(db: str, out: Path) -> Counter:
    store = Store(db)
    if out.exists():
        shutil.rmtree(out)  # always a fresh copy of what is in the database
    counts = Counter()
    for kind, obj in store.kept():
        folder = out / kind
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{obj['id']}.json").write_text(
            json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
        )
        counts[kind] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Export stored HSDS objects as JSON files.")
    parser.add_argument("--db", default="resource_engine.db")
    parser.add_argument("--out", type=Path, default=Path("export"))
    args = parser.parse_args()
    counts = export(args.db, args.out)
    for kind, n in sorted(counts.items()):
        print(f"{n:5}  {args.out / kind}/")
    print(f"{sum(counts.values()):5}  objects exported")


if __name__ == "__main__":
    main()
