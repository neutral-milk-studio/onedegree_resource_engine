"""Sample website source: serves pages from ``data/sample_websites/`` (no network).

The manifest maps each stored service to a fictional ``.example`` URL and either a page
in that folder or a simulated failure (``{"status": 403}``, ``{"error": "..."}``). A URL
that isn't in the manifest is a failed check, never a guess.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from contracts.website_source import WebsiteResult

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = REPO_ROOT / "data" / "sample_websites"


@dataclass(frozen=True)
class SampleSite:
    service_id: str
    url: str
    page: str | None
    simulate: dict
    planted: str
    expected: dict


class SampleWebsiteSource:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or Path(os.environ.get("RE_SAMPLE_WEBSITES_DIR", DEFAULT_DIR))

    @cached_property
    def sites(self) -> list[SampleSite]:
        manifest = json.loads((self.directory / "manifest.json").read_text(encoding="utf-8"))
        return [
            SampleSite(
                service_id=entry["service_id"],
                url=entry["url"],
                page=entry.get("page"),
                simulate=entry.get("simulate") or {},
                planted=entry.get("planted", ""),
                expected=entry.get("expected") or {},
            )
            for entry in manifest["sites"]
        ]

    def url_for(self, service_id: str) -> str | None:
        """The sample URL standing in for this service's website, if it has one."""
        return next((s.url for s in self.sites if s.service_id == service_id), None)

    def service_ids(self) -> list[str]:
        return [s.service_id for s in self.sites]

    def get(self, url: str) -> WebsiteResult:
        site = next((s for s in self.sites if s.url == url), None)
        if site is None:
            return WebsiteResult(None, None, f"No sample page is configured for {url}")
        if site.simulate:
            return WebsiteResult(None, site.simulate.get("status"), site.simulate.get("error"))
        path = self.directory / (site.page or "")
        if not site.page or not path.is_file():
            return WebsiteResult(None, None, f"Sample page file missing: {site.page}")
        return WebsiteResult(path.read_text(encoding="utf-8"), 200, None)
