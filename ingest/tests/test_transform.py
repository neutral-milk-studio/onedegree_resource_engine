"""Transform layer: sample pages -> validated HSDS in the local store, nothing dropped."""

from collections import Counter

import pytest

from contracts.adapter import SourceAdapter
from ingest.pipeline.run import run_ingest
from ingest.pipeline.transform import ndp, odrs
from ingest.pipeline.transform.adapter import MapperAdapter, flatten, group
from ingest.pipeline.transform.hsds import FOREIGN_KEYS, KIND_ORDER, validate
from ingest.pipeline.transform.run import sample_pages
from ingest.pipeline.transform.store import Store

MAPPERS = [ndp, odrs]


def sample_adapter(mapper) -> MapperAdapter:
    return MapperAdapter(mapper, sample_pages(mapper.SOURCE))


def table_counts(store: Store) -> Counter:
    return Counter(kind for kind, _ in store.kept())


@pytest.mark.parametrize("mapper", MAPPERS)
def test_samples_are_found_and_hold_records(mapper):
    assert next(iter(sample_adapter(mapper).fetch()), None) is not None


def test_mapper_adapters_satisfy_the_contract():
    assert isinstance(MapperAdapter(ndp), SourceAdapter)
    assert isinstance(MapperAdapter(odrs), SourceAdapter)


@pytest.mark.parametrize("mapper", MAPPERS)
def test_samples_ingest_reconciled_and_valid(mapper):
    store = Store()
    counts = run_ingest(sample_adapter(mapper), store)

    assert counts.is_reconciled()
    assert counts.stored > 0
    for kind, obj in store.kept():
        assert validate(kind, obj) == [], (kind, obj["id"])


@pytest.mark.parametrize("mapper", MAPPERS)
def test_reingest_updates_instead_of_duplicating(mapper):
    store = Store()
    run_ingest(sample_adapter(mapper), store)
    first = table_counts(store)
    run_ingest(sample_adapter(mapper), store)
    assert table_counts(store) == first


def test_unmappable_record_is_quarantined_and_batch_continues():
    good = next(iter(sample_adapter(ndp).fetch()))
    page = {"data": [{"id": "broken-1"}, good]}  # no "name": the mapper raises
    store = Store()
    counts = run_ingest(MapperAdapter(ndp, [page]), store)

    assert counts.is_reconciled()
    assert counts.stored > 0
    whole_record = [q for q in store.quarantined() if q["kind"] == "record"]
    assert [q["native_id"] for q in whole_record] == ["broken-1"]


def test_kind_order_puts_every_parent_first():
    rank = {kind: i for i, kind in enumerate(KIND_ORDER)}
    for kind, refs in FOREIGN_KEYS.items():
        for _, parent in refs:
            if parent != kind:
                assert rank[parent] < rank[kind], (parent, kind)


@pytest.mark.parametrize("mapper", MAPPERS)
def test_group_then_flatten_keeps_every_object(mapper):
    record = next(iter(sample_adapter(mapper).fetch()))
    pairs = mapper.map(record)
    assert Counter(o["id"] for _, o in flatten(group(pairs))) == Counter(o["id"] for _, o in pairs)


def test_report_builds_and_renders():
    from ingest.pipeline.transform.report import build, render

    report = build(ndp, sample_pages(ndp.SOURCE))
    assert report["records"] and report["records"][0]["tree"]
    assert any("'city' is a required property" in r["reason"] for r in report["reasons"])
    page = render([report])
    assert "__DATA__" not in page and "</script>" in page
