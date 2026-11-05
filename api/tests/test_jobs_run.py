"""api/app/jobs/run.py: the CLI the AWS deploy's scheduled jobs invoke (overrides the
container command as ``python -m api.app.jobs.run <job> --who=... [--limit=N]``, per
infra/terraform/scheduler.tf). Confirms it drives this app's real ingest and maintenance
code, not a stub, and matches the output shape callers can rely on."""

from __future__ import annotations

from api.app.jobs.run import run_import, run_maintenance


def test_run_import_ingests_every_configured_source(store, raw_store, monkeypatch):
    # run_import/run_maintenance import these lazily (so a plain `--help` stays cheap);
    # patch them where they're actually defined, not in api.app.jobs.run's namespace.
    monkeypatch.setattr("shared.db.get_engine", lambda: store.engine)
    monkeypatch.setattr("shared.db.init_db", lambda *a, **k: None)
    monkeypatch.setattr("api.app.store_pg.PgHsdsStore", lambda engine: store)
    result = run_import("scheduler")
    assert result["job"] == "import"
    sources = {s["source"]: s for s in result["sources"]}
    assert sources["odrs"] == {"source": "odrs", "received": 50, "stored": 50, "quarantined": 0}
    assert sources["ndp"]["received"] == 20


def test_run_maintenance_checks_and_reports(loaded_store, monkeypatch):
    monkeypatch.setattr("shared.db.get_engine", lambda: loaded_store.engine)
    monkeypatch.setattr("shared.db.init_db", lambda *a, **k: None)
    monkeypatch.setattr("api.app.store_pg.PgHsdsStore", lambda engine: loaded_store)
    result = run_maintenance("scheduler", limit=None)
    assert result["job"] == "maintenance" and result["checked"] == 11  # every sample site


def test_run_maintenance_respects_the_limit_flag(loaded_store, monkeypatch):
    monkeypatch.setattr("shared.db.get_engine", lambda: loaded_store.engine)
    monkeypatch.setattr("shared.db.init_db", lambda *a, **k: None)
    monkeypatch.setattr("api.app.store_pg.PgHsdsStore", lambda engine: loaded_store)
    result = run_maintenance("scheduler", limit=3)
    assert result["checked"] <= 3


def test_cli_prints_valid_json(capsys, monkeypatch):

    monkeypatch.setattr("sys.argv", ["run.py", "import", "--who=cli-test"])
    monkeypatch.setattr("api.app.jobs.run.run_import", lambda who: {"job": "import", "who": who})
    from api.app.jobs.run import main

    main()
    out = capsys.readouterr().out
    import json

    assert json.loads(out) == {"job": "import", "who": "cli-test"}
