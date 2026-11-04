"""One command demo: generate sample data (if missing), load it, seed demo users/documents, then serve.

python -m api.app.demo            # http://127.0.0.1:8000
python -m api.app.demo --reset    # wipe the local database first
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from api.app.config import ROOT, load_settings


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--no-serve", action="store_true")
    a = ap.parse_args()
    s = load_settings()
    if a.reset and s.data_dir.exists():
        shutil.rmtree(s.data_dir)
    gen = ROOT / "data" / "generated"
    if not (gen / "websites.json").exists():
        from tools.sample_data_generator.generate import generate

        print("Generating sample data:", json.dumps(generate(gen)))
    from api.app.container import build_container
    from api.app.main import create_app
    from tools.sample_data_generator.seed import load_demo_sources, seed_demo

    _container = build_container(s)
    load_demo_sources(_container, gen)
    app = create_app(container=_container)
    out = (
        seed_demo(_container)
        if not _container.users.by_email("admin@demo.org")
        else {"note": "already seeded"}
    )
    print(
        json.dumps(
            {
                k: v
                for k, v in out.items()
                if k in ("credentials", "api_keys", "maintenance", "note")
            },
            indent=2,
        )
    )
    if not a.no_serve:
        print("\nOpen http://127.0.0.1:8000\n")
        import uvicorn

        uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
