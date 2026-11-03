"""Website source contract — the replaceable interface for fetching site content.

Two implementations sit behind this one interface: a sample implementation
(default, reads seeded content, no network) and a live implementation (real HTTP,
behind configuration). Nothing outside the website source knows which runs.

Spec: Requirements 1.1, 19.1, 19.5.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class WebsiteResult:
    """The result of fetching a URL.

    A non-None ``error`` (or an unreachable/expired-cert status) drives the
    ``actively_bad`` / ``check_failed`` categories — never a silent skip.
    """

    content: str | None     # page text, or None on failure
    status: int | None      # HTTP status, or None if the request never completed
    error: str | None       # non-None on failure (timeout, DNS, TLS, etc.)


@runtime_checkable
class WebsiteSource(Protocol):
    """Given a URL, return its content, HTTP status, and any error."""

    def get(self, url: str) -> WebsiteResult:
        ...
