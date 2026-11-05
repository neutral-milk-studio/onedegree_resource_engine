"""Search: query planning, search text, safety, filtering, ranking, and the API.

Runs on SQLite (SimpleSearch) by default and on Postgres full-text search when
RE_TEST_DATABASE_URL is set, so both backends are covered by the same tests.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.app.deps import get_store
from api.app.main import create_app
from search.indexer.build_index import rebuild_index, search_text
from search.query.intent import plan
from search.query.search import _tsquery

AUTH = {"Authorization": "Bearer test-token"}
FAKE_RENT_RELIEF = "e885691b-3136-58c2-82e5-533f924116e4"
DEAD_SITE = "2b18ff78-ba42-5e96-a5a5-5e414a395bca"


def test_plan_keeps_own_words_and_adds_service_words():
    p = plan("I just lost my job and can't pay rent")
    assert p.own_terms == ["lost", "job", "pay", "rent"]
    assert p.needs == ["Rent and housing costs", "Job loss and income"]
    assert {"financial assistance", "housing", "unemployment"} <= set(p.added_terms)
    assert "rent" not in p.added_terms  # already the person's own word


def test_plan_ignores_filler_words():
    assert plan("can you please help me find").own_terms == []
    assert plan("comida gratis").needs == []  # no guessing for words we don't know


def test_tsquery_only_contains_safe_tokens():
    query = _tsquery(["rent'); drop table services;--", "financial assistance"])
    # Only word tokens survive; quotes, parentheses and semicolons never reach Postgres.
    assert query == "(rent:* & drop:* & table:* & services:*) | (financial:* & assistance:*)"


def test_search_text_covers_what_people_search_by():
    doc = {
        "name": "Food Day", "description": "Free groceries", "eligibility_description": "All",
        "organization": {"name": "Free The Need"}, "service_areas": [{"name": "San Francisco"}],
        "languages": [{"name": "Spanish"}],
        "attributes": [
            {"taxonomy_term": {"name": "Food Pantries", "description": "Food > Food Pantries"}}
        ],
        "service_at_locations": [{"location": {"addresses": [{"city": "Daly City"}]}}],
    }  # fmt: skip
    text = search_text(doc)
    for expected in ("Food Day", "Free The Need", "Spanish", "Food Pantries", "Daly City"):
        assert expected in text


@pytest.fixture
def client(loaded_store, raw_store, monkeypatch):
    monkeypatch.setenv("RE_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("RE_RAW_DIR", str(raw_store.root))
    app = create_app(init_database=False)
    app.dependency_overrides[get_store] = lambda: loaded_store
    return TestClient(app)


def search(client, q, **params):
    response = client.get("/search", headers=AUTH, params={"q": q, **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_job_loss_and_rent_finds_rent_and_money_help(client):
    body = search(client, "I just lost my job and can't pay rent", limit=10)
    assert body["total"] > 10 and body["needs"] == ["Rent and housing costs", "Job loss and income"]
    names = " ".join(r["name"].lower() for r in body["results"])
    assert "rent" in names and ("financial" in names or "assistance" in names)
    assert all(r["trust"]["badge"] for r in body["results"])
    scores = [r["score"] for r in body["results"]]
    assert scores == sorted(scores, reverse=True)


def test_flagged_and_dead_listings_are_hidden_and_counted(client):
    before = search(client, "emergency rent relief grants")
    assert FAKE_RENT_RELIEF in {r["service_id"] for r in before["results"]}
    client.post("/maintenance/run", headers=AUTH)  # flags the fake listing, finds the dead site
    after = search(client, "emergency rent relief grants")
    assert FAKE_RENT_RELIEF not in {r["service_id"] for r in after["results"]}
    assert after["hidden"] >= 1 and after["total"] == before["total"] - after["hidden"]
    trees = search(client, "street tree planting")
    assert DEAD_SITE not in {r["service_id"] for r in trees["results"]}


def test_public_search_never_exposes_risk_signals(client):
    """The public API must never expose why a listing was flagged -- only staff review does."""
    client.post("/maintenance/run", headers=AUTH)
    body = search(client, "emergency rent relief grants")
    for result in body["results"]:
        assert "risk_signals" not in result and "risk_score" not in result


def test_inactive_listings_are_not_returned(client, loaded_store):
    from shared.models import Service

    with loaded_store.session() as session:
        session.get(Service, DEAD_SITE).status = "inactive"
    body = search(client, "street tree planting")
    assert DEAD_SITE not in {r["service_id"] for r in body["results"]} and body["hidden"] >= 1


def test_verified_badge_after_a_check(client):
    client.post("/maintenance/run", headers=AUTH)
    body = search(client, "rent utility assistance coastside")
    coastside = next(r for r in body["results"] if r["organization_name"] == "Coastside Hope")
    assert (
        coastside["trust"]["badge"].startswith("Verified") and coastside["trust"]["level"] == "high"
    )


def test_snippet_and_pagination(client):
    first = search(client, "food", limit=3)
    second = search(client, "food", limit=3, offset=3)
    assert len(first["results"]) == 3
    assert not {r["service_id"] for r in first["results"]} & {
        r["service_id"] for r in second["results"]
    }
    assert all(r["snippet"] for r in first["results"])


def test_no_results_and_bad_input(client):
    assert search(client, "zzzzqqq")["results"] == []
    assert client.get("/search", headers=AUTH, params={"q": "a"}).status_code == 422
    assert client.get("/search", headers=AUTH).status_code == 422
    assert client.get("/search", params={"q": "food"}).status_code in {401, 403}


def test_rebuild_index_is_idempotent(loaded_store):
    assert rebuild_index(loaded_store) == 0  # every save already keeps search text current


def test_results_carry_coordinates_when_the_location_has_them(client):
    body = search(client, "street tree planting")
    with_coords = [r for r in body["results"] if r["latitude"] is not None]
    assert with_coords  # NDP sample data includes real coordinates
    for result in with_coords:
        assert -90 <= result["latitude"] <= 90
        assert -180 <= result["longitude"] <= 180


def test_get_backend_honors_re_search_backend(loaded_store, monkeypatch):
    from search.query.search import PostgresSearch, SimpleSearch, get_backend
    from search.query.semantic import SemanticSearch

    monkeypatch.setenv("RE_SEARCH_BACKEND", "semantic")
    assert isinstance(get_backend(loaded_store), SemanticSearch)
    monkeypatch.setenv("RE_SEARCH_BACKEND", "simple")
    assert isinstance(get_backend(loaded_store), SimpleSearch)
    monkeypatch.delenv("RE_SEARCH_BACKEND")
    backend = get_backend(loaded_store)
    expected = PostgresSearch if loaded_store.engine.dialect.name == "postgresql" else SimpleSearch
    assert isinstance(backend, expected)
