"""On-demand ingest orchestration — STUB (task 15).

Triggered by a human onboarding a source (NO recurring scheduler). Writes raw,
transforms, validates, stores/quarantines, and returns reconciled counts.
Spec: Requirements 9.1, 9.2, 13.2, 13.3, 13.4.
"""

from __future__ import annotations

from contracts.adapter import SourceAdapter
from contracts.store import HsdsStore, IngestCounts


def run_ingest(adapter: SourceAdapter, store: HsdsStore) -> IngestCounts:
    """Run one on-demand ingest for a source; return received/stored/quarantined."""
    raise NotImplementedError("task 15: orchestrate on-demand ingest; received == stored + quarantined")
