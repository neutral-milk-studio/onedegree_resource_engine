"""Sample website source — STUB (task 18.1).

Default implementation: reads seeded content from seed/website_content/ (no
network), including seeded discrepancies and failures. Spec: Requirements 19.2, 19.3.
"""

from __future__ import annotations

from contracts.website_source import WebsiteResult


class SampleWebsiteSource:
    def get(self, url: str) -> WebsiteResult:
        raise NotImplementedError("task 18.1: return seeded content for url via manifest")
