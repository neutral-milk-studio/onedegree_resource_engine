"""The validator must actually reject bad HSDS, not just pass everything."""

import pytest

from transform.hsds import clean, hsds_id, split_phone, to_number, validate

SVC = "6f1c2a52-6b0e-4a5e-9d3a-2f3b7d0c9e41"


def test_valid_service_passes():
    assert validate("service", {"id": SVC, "organization_id": SVC, "name": "Food", "status": "active"}) == []


@pytest.mark.parametrize("obj, expected", [
    ({"name": "Food", "status": "active"}, "'id' is a required property"),
    ({"id": SVC, "name": "Food", "status": "open"}, "status: 'open' is not one of"),
    ({"id": "22560", "name": "Food", "status": "active"}, "id: '22560' is not a 'uuid'"),
    ({"id": SVC, "name": "Food", "status": "active", "url": "not a url"}, "url: 'not a url' is not a 'uri'"),
    ({"id": SVC, "name": "Food", "status": "active", "minimum_age": "18"}, "minimum_age: '18' is not of type 'number'"),
])
def test_invalid_service_fails(obj, expected):
    errors = validate("service", obj)
    assert any(expected in e for e in errors), errors


def test_nested_refs_resolve():
    # service.phones -> phone.json; a bad nested phone must be caught.
    errors = validate("service", {"id": SVC, "name": "F", "status": "active", "phones": [{"id": SVC}]})
    assert any("phones/0" in e and "'number' is a required property" in e for e in errors), errors


def test_address_requires_city_and_postal_code():
    errors = validate("address", {"id": SVC, "address_1": "Various Locations", "state_province": "CA",
                                  "country": "US", "address_type": "physical"})
    assert any("'city'" in e for e in errors) and any("'postal_code'" in e for e in errors)


def test_unknown_kind():
    assert validate("nonsense", {}) != []


def test_hsds_id_is_deterministic_and_scoped():
    assert hsds_id("odrs", "service", 1) == hsds_id("odrs", "service", "1")
    assert hsds_id("odrs", "service", 1) != hsds_id("odrs", "phone", 1)
    assert hsds_id("odrs", "service", 1) != hsds_id("ndp", "service", 1)


def test_clean_drops_empty_values():
    assert clean({"a": None, "b": "", "c": [], "d": {}, "e": " x ", "f": 0, "g": False, "h": [None, "y"]}) == {
        "e": "x", "f": 0, "g": False, "h": ["y"],
    }


@pytest.mark.parametrize("raw, expected", [
    ("650-276-4101 x 4", ("650-276-4101", 4)),
    ("510-379-0929 x 935", ("510-379-0929", 935)),
    ("510-379-0929 ext. 12", ("510-379-0929", 12)),
    ("510-613-0330", ("510-613-0330", None)),
])
def test_split_phone(raw, expected):
    assert split_phone(raw) == expected


def test_to_number():
    assert to_number("18") == 18
    assert to_number(None) is None
    assert to_number("eighteen") is None


def test_date_time_format_is_checked():
    base = {"id": SVC, "organization_id": SVC, "name": "F", "status": "active"}
    assert validate("service", {**base, "last_modified": "2025-06-13T15:51:07.000Z"}) == []
    assert any("is not a 'date-time'" in e for e in validate("service", {**base, "last_modified": "June 13"}))
