"""LiveWebsiteSource: real HTTP against a real local server (no mocked transport), so an
actual timeout, redirect, or robots.txt bug would be caught.
"""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from maintenance.website_source import get_website_source
from maintenance.website_source.live import LiveWebsiteSource

PAGE = b"<html><body><p>Open Monday-Friday, 9am-5pm. Call (555) 111-2222.</p></body></html>"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a) -> None:  # noqa: D401 - silence the test server's own logging
        pass

    def do_GET(self) -> None:  # noqa: N802 - stdlib's naming
        if self.path == "/robots.txt":
            self._send(200, b"User-agent: *\nDisallow: /blocked\n")
        elif self.path == "/ok":
            self._send(200, PAGE, content_type="text/html")
        elif self.path == "/blocked":
            self._send(200, PAGE, content_type="text/html")
        elif self.path == "/gone":
            self._send(404, b"not found")
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.end_headers()
        elif self.path == "/big":
            self._send(200, PAGE * 10_000, content_type="text/html")
        elif self.path == "/image":
            self._send(200, b"\x89PNG", content_type="image/png")
        elif self.path == "/slow":
            time.sleep(0.5)
            self._send(200, PAGE, content_type="text/html")
        else:
            self._send(404, b"not found")

    def _send(self, status: int, body: bytes, content_type: str = "text/plain") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    thread.join()


@pytest.fixture
def source(monkeypatch):
    monkeypatch.setenv("RE_LIVE_FETCH_INTERVAL_S", "0")
    with LiveWebsiteSource() as src:
        yield src


def test_fetches_a_real_page_over_the_network(source, server):
    result = source.get(f"{server}/ok")
    assert result.status == 200 and result.error is None
    assert b"555" in result.content.encode() or "555" in result.content


def test_honors_robots_txt_disallow(source, server):
    result = source.get(f"{server}/blocked")
    assert result.content is None
    assert "robots.txt" in result.error


def test_gone_page_reports_404(source, server):
    result = source.get(f"{server}/gone")
    assert result.status == 404
    assert result.content is None


def test_follows_redirects(source, server):
    result = source.get(f"{server}/redirect")
    assert result.status == 200
    assert "555" in result.content


def test_rejects_non_html_content_type(source, server):
    result = source.get(f"{server}/image")
    assert result.content is None
    assert "image/png" in result.error


def test_connection_failure_is_a_clear_error_not_a_crash(source):
    result = source.get("http://127.0.0.1:1/unreachable")
    assert result.content is None and result.status is None
    assert result.error


def test_truncates_oversized_pages(monkeypatch, server):
    monkeypatch.setenv("RE_LIVE_FETCH_INTERVAL_S", "0")
    monkeypatch.setenv("RE_LIVE_FETCH_MAX_BYTES", "1000")
    with LiveWebsiteSource() as src:
        result = src.get(f"{server}/big")
    assert result.status == 200
    assert len(result.content) < len(PAGE) * 10_000


def test_rate_limits_repeated_requests_to_the_same_host(monkeypatch, server):
    monkeypatch.setenv("RE_LIVE_FETCH_INTERVAL_S", "0.3")
    with LiveWebsiteSource() as src:
        src.get(f"{server}/ok")
        started = time.monotonic()
        src.get(f"{server}/ok")
        assert time.monotonic() - started >= 0.25


def test_factory_picks_live_when_configured(monkeypatch):
    monkeypatch.setenv("RE_WEBSITE_SOURCE", "live")
    assert isinstance(get_website_source(store=object()), LiveWebsiteSource)


def test_factory_defaults_to_sample(monkeypatch):
    monkeypatch.delenv("RE_WEBSITE_SOURCE", raising=False)
    from maintenance.website_source.sample import SampleWebsiteSource

    assert isinstance(get_website_source(), SampleWebsiteSource)


def test_service_ids_orders_unchecked_services_first(store):
    """Real DB query: active services with a website, unchecked ones before checked ones."""
    from shared.models import Check, Service

    with store.session() as session:
        session.add_all(
            [
                Service(
                    id="s1", source="t", native_id="1", record_native_id="1", name="A",
                    status="active", website="http://a.example", document={}, content_hash="h1",
                ),
                Service(
                    id="s2", source="t", native_id="2", record_native_id="2", name="B",
                    status="active", website="http://b.example", document={}, content_hash="h2",
                ),
                Service(
                    id="s3", source="t", native_id="3", record_native_id="3", name="C",
                    status="active", website=None, document={}, content_hash="h3",
                ),
            ]
        )  # fmt: skip
        session.flush()
        session.add(Check(service_id="s1", category="verified"))

    src = LiveWebsiteSource(store, limit=None)
    ids = src.service_ids()
    assert ids == ["s2", "s1"]  # unchecked (s2) before checked (s1); s3 has no website
