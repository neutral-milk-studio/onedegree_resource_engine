"""Job entry point: python -m api.app.jobs.run import|maintenance|reindex|seed-demo [options]
Used by ECS scheduled tasks (EventBridge Scheduler), by the Makefile and by operators."""

from __future__ import annotations

import argparse
import json

from api.app.container import build_container


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="api.app.jobs.run")
    ap.add_argument("job", choices=["import", "maintenance", "reindex", "seed-demo"])
    ap.add_argument("--source")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--scope", default="all")
    ap.add_argument("--service-id")
    ap.add_argument("--who", default="scheduler")
    a = ap.parse_args(argv)
    c = build_container()
    if a.job == "seed-demo":
        from tools.sample_data_generator.seed import seed_demo

        print(json.dumps(seed_demo(c), indent=2))
        return 0
    from api.app.jobs.runner import JobRunner

    out = JobRunner(c).start(
        a.job,
        wait=True,
        source=a.source,
        limit=a.limit,
        force=a.force,
        scope=a.scope,
        service_id=a.service_id,
        who=a.who,
    )
    print(json.dumps(out, indent=2, default=str))
    failed = [r for r in out.get("results", []) if r.get("status") == "failed"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
