"""Live website source: real HTTP fetch behind the ``WebsiteSource`` protocol.

Honest, identifiable user agent; robots.txt (RFC 9309) honored per host; at most one
request per host every ``RE_LIVE_FETCH_INTERVAL_S`` seconds; and a capped read so one slow
or oversized page can't stall the rest of a maintenance run. Turned on with
``RE_WEBSITE_SOURCE=live`` (see :mod:`maintenance.website_source`).

Any failure to fetch or read the page becomes a ``WebsiteResult`` error, exactly like the
sample source's simulated failures — ``maintenance.comparator.compare.site_failure``
already turns that into a ``check_failed`` or ``actively_bad`` finding, never a guess.
"""

from __future__ import annotations

import logging
import os
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
from sqlalchemy import func, select

from contracts.website_source import WebsiteResult

log = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "OneDegreeResourceEngineBot/0.1 "
    "(+https://github.com/neutral-milk-studio/onedegree_resource_engine; "
    "checks a nonprofit resource directory listing against this site)"
)


class LiveWebsiteSource:
    def __init__(self, store=None, limit: int | None = None, client: httpx.Client | None = None):
        self.store = store
        self.limit = limit
        if self.limit is None:
            self.limit = int(os.environ.get("RE_LIVE_FETCH_LIMIT", "25"))
        self.user_agent = os.environ.get("RE_LIVE_FETCH_USER_AGENT", DEFAULT_USER_AGENT)
        self.timeout_s = float(os.environ.get("RE_LIVE_FETCH_TIMEOUT_S", "10"))
        self.max_bytes = int(os.environ.get("RE_LIVE_FETCH_MAX_BYTES", str(2 * 1024 * 1024)))
        self.min_interval_s = float(os.environ.get("RE_LIVE_FETCH_INTERVAL_S", "2"))
        self._client = client or httpx.Client()
        self._robots: dict[str, RobotFileParser] = {}
        self._last_fetch: dict[str, float] = {}

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> LiveWebsiteSource:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def service_ids(self) -> list[str]:
        """Every active service with a website, oldest-checked first, capped at ``limit``.

        There's no fixed manifest in live mode (unlike the sample source), so this is how
        a maintenance run picks what to check: real sites, not a canned list.
        """
        from shared.models import Check, Service

        if self.store is None:
            raise RuntimeError("LiveWebsiteSource needs a store to pick which services to check")
        with self.store.session() as session:
            last_checked = (
                select(Check.service_id, func.max(Check.checked_at).label("last"))
                .group_by(Check.service_id)
                .subquery()
            )
            stmt = (
                select(Service.id)
                .outerjoin(last_checked, last_checked.c.service_id == Service.id)
                .where(Service.website.isnot(None), Service.status == "active")
                .order_by(
                    last_checked.c.last.is_(None).desc(), last_checked.c.last.asc(), Service.id
                )
            )
            if self.limit:
                stmt = stmt.limit(self.limit)
            return [row[0] for row in session.execute(stmt).all()]

    def _robots_allows(self, url: str) -> bool:
        parsed = urlparse(url)
        netloc = parsed.netloc
        if netloc not in self._robots:
            parser = RobotFileParser()
            try:
                response = self._client.get(
                    f"{parsed.scheme}://{netloc}/robots.txt",
                    headers={"User-Agent": self.user_agent},
                    timeout=self.timeout_s,
                )
                parser.parse(response.text.splitlines() if response.status_code < 400 else [])
            except httpx.HTTPError:
                parser.parse([])  # can't reach robots.txt: don't block the real check on it
            self._robots[netloc] = parser
        return self._robots[netloc].can_fetch(self.user_agent, url)

    def _wait_turn(self, netloc: str) -> None:
        last = self._last_fetch.get(netloc)
        now = time.monotonic()
        if last is not None and (wait := self.min_interval_s - (now - last)) > 0:
            time.sleep(wait)
        self._last_fetch[netloc] = time.monotonic()

    def get(self, url: str) -> WebsiteResult:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return WebsiteResult(None, None, f"Not a fetchable URL: {url!r}")

        try:
            if not self._robots_allows(url):
                return WebsiteResult(None, None, "Disallowed by the site's robots.txt")
            self._wait_turn(parsed.netloc)
            with self._client.stream(
                "GET",
                url,
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout_s,
                follow_redirects=True,
            ) as response:
                content_type = response.headers.get("content-type", "")
                if content_type and "html" not in content_type and "text" not in content_type:
                    reason = f"Not an HTML page: {content_type}"
                    return WebsiteResult(None, response.status_code, reason)
                chunks, total = [], 0
                for chunk in response.iter_bytes():
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= self.max_bytes:
                        break
                body = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
        except httpx.TimeoutException as exc:
            return WebsiteResult(None, None, f"Timed out: {exc}")
        except httpx.HTTPError as exc:
            return WebsiteResult(None, None, str(exc) or type(exc).__name__)

        if not (200 <= response.status_code < 300):
            return WebsiteResult(None, response.status_code, f"HTTP {response.status_code}")
        if not body.strip():
            return WebsiteResult(None, response.status_code, "No content returned")
        return WebsiteResult(body, response.status_code, None)
