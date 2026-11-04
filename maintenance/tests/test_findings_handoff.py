import pytest

from contracts.findings import (
    Category,
    Evidence,
    Finding,
    FindingRejected,
    FindingsSink,
    finding_problems,
)
from maintenance.findings_store.memory import InMemoryFindingsStore

QUOTE = Evidence(url="https://example.org", snippet="Open to two counties.", model="model-1")
FAILURE = Evidence(url="https://example.org", snippet="HTTP 404")


def finding(**overrides) -> Finding:
    values = {
        "id": "finding-1",
        "service_id": "service-1",
        "field": "eligibility_description",
        "current_value": "One county.",
        "proposed_value": "Two counties.",
        "category": Category.DISCREPANCY,
        "confidence": 0.9,
        "model_id": "model-1",
        "checked_at": "2026-11-03T16:00:00Z",
        "evidence": [QUOTE],
    }
    return Finding(**{**values, **overrides})


@pytest.mark.parametrize(
    "valid",
    [
        finding(),
        finding(category=Category.MISSING_INFORMATION, current_value=None),
        finding(category=Category.VERIFIED, field=None, current_value=None, proposed_value=None),
        finding(
            category=Category.ACTIVELY_BAD,
            field=None,
            current_value=None,
            proposed_value=None,
            model_id="",
            evidence=[FAILURE],
        ),
        finding(
            category=Category.CHECK_FAILED,
            field=None,
            current_value=None,
            proposed_value=None,
            model_id="",
            evidence=[FAILURE],
        ),
    ],
)
def test_valid_findings_are_stored(valid):
    store = InMemoryFindingsStore()
    assert store.save(valid) == valid.id
    assert store.findings[valid.id] is valid


@pytest.mark.parametrize(
    ("invalid", "message"),
    [
        (finding(evidence=[]), "snippet"),
        (finding(evidence=[Evidence(url="https://example.org", snippet="  ")]), "snippet"),
        (finding(evidence=[Evidence(url="", snippet="text")]), "url"),
        (finding(field=None), "needs a field"),
        (finding(model_id=""), "needs a model_id"),
        (finding(proposed_value=None), "needs a proposed_value"),
        (finding(confidence=1.5), "confidence"),
        (finding(category=Category.VERIFIED), "can't have a proposed_value"),
    ],
)
def test_invalid_findings_are_rejected_and_not_stored(invalid, message):
    assert any(message in problem for problem in finding_problems(invalid))
    store = InMemoryFindingsStore()
    with pytest.raises(FindingRejected):
        store.save(invalid)
    assert store.findings == {}


def test_memory_store_matches_the_sink_interface():
    assert isinstance(InMemoryFindingsStore(), FindingsSink)
