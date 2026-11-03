"""Maintenance worker loop — STUB (task 22 / 25).

select-due -> fetch (website source) -> extract (Bedrock) -> compare -> finding.
Records last-checked time + result in the check_log ops table.
Spec: Requirements 18.1, 18.4, 20, 21, 22, 23.
"""

from __future__ import annotations

from contracts.store import HsdsStore
from contracts.website_source import WebsiteSource


def run_checks(store: HsdsStore, website_source: WebsiteSource) -> None:
    """Run maintenance checks for records due, writing findings and the check log."""
    raise NotImplementedError("task 22/25: select-due -> fetch -> extract -> compare -> finding")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit("maintenance worker stub — implemented in the AI Maintenance stream")
