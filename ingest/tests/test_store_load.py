"""The real samples reach the store with the same counts as the transform, and re-run cleanly."""

import pytest

from api.app.services.ingest import SourceConfig, assemble
from api.tests.helpers import make_container
from ingest.pipeline.transform import ndp
from ingest.pipeline.transform.adapter import MapperAdapter, flatten
from ingest.pipeline.transform.run import sample_pages

SOURCES = [
    SourceConfig("odrs", "odrs", "data/samples/NDP/BayesExport_*.json"),
    SourceConfig("ndp", "ndp", "data/samples/NDP/national-data-platform-sample-*.json"),
]


@pytest.mark.parametrize("cfg", SOURCES, ids=lambda cfg: cfg.name)
def test_counts_balance_and_rerun_changes_nothing(cfg):
    c = make_container()
    first = c.ingest.run(cfg, "t", "t")
    assert first["status"] == "completed"
    assert first["received"] == first["stored"] + first["quarantined"]
    before = c.store.counts()
    second = c.ingest.run(cfg, "t", "t")
    assert (second["stored"], second["quarantined"]) == (first["stored"], first["quarantined"])
    assert c.store.counts() == before
    assert (
        c.db.one("SELECT COUNT(*) AS n FROM change_history WHERE last_action_type='update'")["n"]
        == 0
    )


def test_every_stored_document_is_valid_hsds():
    c = make_container()
    for cfg in SOURCES:
        c.ingest.run(cfg, "t", "t")
    for kind in ("organization", "service", "location", "service_at_location"):
        for row in c.store.all_of_type(kind):
            assert c.validator.errors(kind, row["data"]) == []


def test_assemble_puts_children_inside_their_parents():
    adapter = MapperAdapter(ndp, sample_pages("ndp"))
    objects = flatten(adapter.map(next(iter(adapter.fetch()))))
    resources, orphans = assemble(objects)
    assert orphans == []
    kinds = {res.type for res in resources}
    assert "phone" not in kinds and "service" in kinds
    assert all(res.org_id for res in resources if res.type == "service")


def test_source_edit_after_a_person_changed_the_field_raises_a_clash():
    c = make_container()
    cfg = SOURCES[1]
    c.ingest.run(cfg, "t", "t")
    svc = c.store.list("service", limit=1)[0][0]
    c.db.run(
        "UPDATE resource_source SET snapshot=? WHERE resource_id=?",
        ('{"name": "older source name"}', svc["id"]),
    )
    c.store.apply_patch(svc["id"], {"name": "A person fixed this"}, "admin", "a")
    run = c.ingest.run(cfg, "t", "t")
    assert run["clashes"] >= 1
    assert c.store.get(svc["id"])["data"]["name"] == "A person fixed this"
    assert c.proposals.list(status="pending", kind="conflict", resource_id=svc["id"])
