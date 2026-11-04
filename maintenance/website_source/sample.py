"""Sample website source (task 18.1).

Default implementation behind the ``WebsiteSource`` contract. It reads the seeded
pages in ``seed/website_content/`` through ``manifest.json`` and never touches the
network. Nothing outside this module needs to know it is the sample one.

Spec: Requirements 19.1, 19.2, 19.3, 19.5.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from contracts.website_source import WebsiteResult

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTENT_DIR = REPO_ROOT / "seed" / "website_content"
CONTENT_DIR_ENV = "RE_SEED_CONTENT_DIR"

# The seeded pages carry an HTML comment that names the expected category. A real
# site never would, and a model must not be able to read the answer, so it is removed.
# This is a heuristic for our own fixtures, not an HTML sanitizer.
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# A comment that is never closed would otherwise leak, so drop it to the end of the page.
_UNTERMINATED_COMMENT = re.compile(r"<!--.*\Z", re.DOTALL)


def strip_comments(html: str) -> str:
    """Remove HTML comments, including one that is never closed."""
    return _UNTERMINATED_COMMENT.sub("", _HTML_COMMENT.sub("", html))


def normalize_url(url: str) -> str:
    """Return a comparable form of ``url``.

    The scheme and host are lowercased, and the fragment and trailing slash are dropped.
    The path keeps its case, because paths are case-sensitive.
    """
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}{query}"


def _content_dir() -> Path:
    configured = os.environ.get(CONTENT_DIR_ENV)
    if not configured:
        return DEFAULT_CONTENT_DIR
    path = Path(configured)
    return path if path.is_absolute() else REPO_ROOT / path


class SampleWebsiteSource:
    """Serve seeded pages and seeded failures from the manifest."""

    def __init__(self, content_dir: Path | None = None) -> None:
        self._dir = (content_dir or _content_dir()).resolve()
        # A missing or malformed manifest raises here, so a bad setup fails at startup.
        manifest = json.loads((self._dir / "manifest.json").read_text(encoding="utf-8"))
        # url -> page filename, or None for a seeded unreachable site.
        self._pages: dict[str, str | None] = {}
        for entry in manifest["entries"]:
            key = normalize_url(entry["url"])
            if key in self._pages:
                raise ValueError(f"duplicate manifest url: {entry['url']}")
            self._pages[key] = entry.get("page")

    def knows(self, url: str) -> bool:
        """True when the manifest has an entry for ``url``, seeded failures included."""
        return normalize_url(url) in self._pages

    def get(self, url: str) -> WebsiteResult:
        key = normalize_url(url)
        if key not in self._pages:
            return WebsiteResult(content=None, status=404, error="not found")
        page = self._pages[key]
        if page is None:
            return WebsiteResult(content=None, status=None, error="unreachable")
        return self._read_page(page)

    def _read_page(self, page: str) -> WebsiteResult:
        path = (self._dir / page).resolve()
        # A manifest entry must not be able to read files outside the content folder.
        if not path.is_relative_to(self._dir):
            return WebsiteResult(content=None, status=None, error="sample page outside content dir")
        try:
            html = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # Name the page, not the absolute path, so logs and findings stay portable.
            return WebsiteResult(content=None, status=None, error=f"sample page unreadable: {page}")
        return WebsiteResult(content=strip_comments(html), status=200, error=None)
