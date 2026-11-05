"""POST /sms/inbound: text-based search, Twilio's inbound-webhook shape in, TwiML out.
Runs against the real sample data (loaded_store) through the real search backend --
only the webhook transport (form fields, token, XML) is new here."""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.app.deps import get_store
from api.app.main import create_app
from api.app.routers.sms import reply_text


def client(loaded_store, raw_store, monkeypatch, webhook_token="sms-secret"):
    monkeypatch.setenv("RE_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("RE_SMS_WEBHOOK_TOKEN", webhook_token)
    monkeypatch.setenv("RE_RAW_DIR", str(raw_store.root))
    app = create_app(init_database=False)
    app.dependency_overrides[get_store] = lambda: loaded_store
    return TestClient(app)


def test_reply_text_lists_up_to_three_resources(loaded_store):
    reply = reply_text(loaded_store, "I just lost my job and can't pay rent")
    assert reply.startswith("Resources:\n")
    assert len(reply.splitlines()) <= 4  # header + up to 3 results


def test_reply_text_handles_no_matches(loaded_store):
    assert "No matches" in reply_text(loaded_store, "zzzqqqnonsense")


def test_reply_text_handles_empty_message(loaded_store):
    assert "Text us what you need" in reply_text(loaded_store, "")
    assert "Text us what you need" in reply_text(loaded_store, "a")


def test_webhook_returns_twiml_with_a_reply(loaded_store, raw_store, monkeypatch):
    c = client(loaded_store, raw_store, monkeypatch)
    response = c.post(
        "/sms/inbound",
        params={"token": "sms-secret"},
        data={"From": "+15105550100", "Body": "food pantry"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "<Response><Message>" in response.text
    assert "Resources:" in response.text or "No matches" in response.text


def test_webhook_rejects_a_missing_or_wrong_token(loaded_store, raw_store, monkeypatch):
    c = client(loaded_store, raw_store, monkeypatch)
    no_token = c.post("/sms/inbound", data={"From": "+1", "Body": "food"})
    assert no_token.status_code == 401
    wrong_token = c.post(
        "/sms/inbound", params={"token": "nope"}, data={"From": "+1", "Body": "food"}
    )
    assert wrong_token.status_code == 401


def test_webhook_disabled_when_no_token_is_configured(loaded_store, raw_store, monkeypatch):
    monkeypatch.setenv("RE_BEARER_TOKEN", "test-token")
    monkeypatch.delenv("RE_SMS_WEBHOOK_TOKEN", raising=False)
    monkeypatch.setenv("RE_RAW_DIR", str(raw_store.root))
    app = create_app(init_database=False)
    app.dependency_overrides[get_store] = lambda: loaded_store
    c = TestClient(app)
    response = c.post(
        "/sms/inbound", params={"token": "anything"}, data={"From": "+1", "Body": "food"}
    )
    assert response.status_code == 401


def test_reply_text_never_includes_a_flagged_listing(loaded_store, raw_store, monkeypatch):
    """Same filter as the web search and the public API: a suspicious finding hides a
    listing from SMS too, once maintenance has flagged it."""
    c = client(loaded_store, raw_store, monkeypatch)
    c.post("/maintenance/run", headers={"Authorization": "Bearer test-token"})
    reply = reply_text(loaded_store, "I can't pay rent")
    assert "Emergency Rent Relief Grants" not in reply


def test_webhook_needs_no_bearer_token(loaded_store, raw_store, monkeypatch):
    """Twilio can't send one -- only the dashboard's other endpoints require it."""
    c = client(loaded_store, raw_store, monkeypatch)
    response = c.post(
        "/sms/inbound", params={"token": "sms-secret"}, data={"From": "+1", "Body": "food"}
    )
    assert response.status_code == 200
