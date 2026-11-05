"""Compatibility CLI for the AWS deploy's scheduled jobs.

``infra/terraform/scheduler.tf`` runs two EventBridge Scheduler jobs as one-off Fargate
tasks, overriding the container command to ``python -m api.app.jobs.run <job> --who=... [--limit=N]``.
That module path and flag shape come from the other team's app (``api/app/{config,
container,...}.py``, excluded from this project's lint/test scope — see pyproject.toml).
This is a thin wrapper in *this* app's own package (so it ships in the same Docker image
built from ``api/Dockerfile``) that answers to the same CLI shape by calling this app's
real ingest and maintenance code — no infra change needed to run correctly.
"""

from __future__ import annotations

import argparse
import json
import logging


def run_import(who: str) -> dict:
    from api.app.store_pg import PgHsdsStore
    from ingest.adapters.registry import get_adapter, source_names
    from ingest.pipeline.run import run_ingest
    from shared.db import get_engine, init_db

    init_db(get_engine(), retries=10)
    store = PgHsdsStore(get_engine())
    reports = [run_ingest(get_adapter(name), store, triggered_by=who) for name in source_names()]
    return {
        "job": "import",
        "sources": [
            {"source": r.source, "received": r.received, "stored": r.stored,
             "quarantined": r.quarantined} for r in reports
        ],  # fmt: skip
    }


def run_maintenance(who: str, limit: int | None) -> dict:
    from dataclasses import asdict

    from api.app.store_pg import PgHsdsStore
    from maintenance.worker.run_check import run_checks
    from shared.db import get_engine, init_db

    init_db(get_engine(), retries=10)
    store = PgHsdsStore(get_engine())
    service_ids = None
    if limit is not None:
        from sqlalchemy import select

        from shared.models import Service

        with store.session() as session:
            service_ids = list(
                session.scalars(select(Service.id).order_by(Service.updated_at).limit(limit))
            )
    report = run_checks(store, service_ids=service_ids, actor=who)
    return {"job": "maintenance", **{k: v for k, v in asdict(report).items() if k != "not_found"}}


def main() -> None:
    parser = argparse.ArgumentParser(description="Scheduled job entry point (AWS deploy).")
    parser.add_argument("job", choices=["import", "maintenance"])
    parser.add_argument("--who", default="scheduler")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    result = run_import(args.who) if args.job == "import" else run_maintenance(args.who, args.limit)
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
