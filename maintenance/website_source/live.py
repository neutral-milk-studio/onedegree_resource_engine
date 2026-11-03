"""Live website source — STUB (task 18.1, not required for MVP).

Real HTTP behind the same interface: honest user agent, robots.txt, per-site
rate limits. Turned on by configuration. Spec: Requirement 19.4.
"""

from __future__ import annotations

from contracts.website_source import WebsiteResult


class LiveWebsiteSource:
    def get(self, url: str) -> WebsiteResult:
        raise NotImplementedError("live fetch is not required for the MVP (task 18.1)")
