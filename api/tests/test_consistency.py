"""Writes can't be lost: decisions and edits are compare-and-swap, in one transaction."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from api.app.services.store import Conflict
from api.tests.helpers import make_container
from api.app.services.ingest import SourceConfig


@pytest.fixture
def loaded():
    c = make_container()
    c.ingest.run(
        SourceConfig("ndp", "ndp", "data/samples/NDP/national-data-platform-sample-1.json"),
        "t",
        "t",
    )
    return c, c.store.list("service", limit=1)[0][0]


def proposal(c, svc, value="A fee"):
    return c.proposals.create(
        resource_id=svc["id"],
        resource_type="service",
        org_id=svc["org_id"],
        kind="provider_update",
        proposed_by_type="provider",
        proposed_by="p",
        patch={
            "fees_description": {"current": svc["data"].get("fees_description"), "proposed": value}
        },
    )


def test_a_change_request_can_be_decided_only_once(loaded):
    c, svc = loaded
    cid = proposal(c, svc)
    c.proposals.approve(cid, "first")
    with pytest.raises(Conflict):
        c.proposals.approve(cid, "second")
    with pytest.raises(Conflict):
        c.proposals.reject(cid, "third")
    assert c.proposals.get(cid)["decided_by"] == "first"


def test_a_rejected_change_cannot_be_applied(loaded):
    c, svc = loaded
    cid = proposal(c, svc)
    c.proposals.reject(cid, "admin")
    with pytest.raises(Conflict):
        c.proposals.approve(cid, "admin")
    assert c.store.get(svc["id"])["version"] == svc["version"]


def test_concurrent_approvals_apply_once(loaded):
    c, svc = loaded
    cid = proposal(c, svc)

    def approve(name):
        try:
            c.proposals.approve(cid, name)
            return "applied"
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(approve, ["a", "b", "c", "d"]))
    assert sorted(results) == ["applied", "conflict", "conflict", "conflict"]
    assert c.store.get(svc["id"])["version"] == svc["version"] + 1


def test_every_saved_edit_gets_its_own_version(loaded):
    c, svc = loaded

    def edit(n):
        try:
            return c.store.apply_patch(svc["id"], {"alert": f"value {n}"}, "admin", "a")["version"]
        except Conflict:
            return None

    with ThreadPoolExecutor(8) as pool:
        versions = [v for v in pool.map(edit, range(8)) if v is not None]
    assert len(versions) == len(set(versions))
    history = [h for h in c.store.history(svc["id"]) if h["field_name"] == "alert"]
    assert len(history) == len(versions)
    assert c.store.get(svc["id"])["version"] == svc["version"] + len(versions)


def test_a_stale_expected_version_saves_nothing(loaded):
    c, svc = loaded
    c.store.apply_patch(svc["id"], {"alert": "one"}, "admin", "a")
    with pytest.raises(Conflict):
        c.store.apply_patch(
            svc["id"], {"alert": "two"}, "admin", "a", expected_version=svc["version"]
        )
    assert c.store.get(svc["id"])["data"]["alert"] == "one"
