"""On-demand ingest orchestration.

Triggered by a human onboarding a source (NO recurring scheduler). Transforms,
validates, stores or quarantines, and returns reconciled counts. The work is done
by ingest.pipeline.transform; this is the entry point the rest of the system calls.
Spec: Requirements 9.1, 9.2, 13.2, 13.3, 13.4.

Not done here yet: writing each raw payload to S3 before transforming
(ingest.raw_store, task 12), and the production HsdsStore (separate workstream).
Until then `store` is the local SQLite store in the HSDS table layout.
"""

from __future__ import annotations

from contracts.adapter import SourceAdapter
from contracts.store import IngestCounts
from ingest.pipeline.transform.run import run
from ingest.pipeline.transform.store import Store


def run_ingest(adapter: SourceAdapter, store: Store) -> IngestCounts:
    """Run one on-demand ingest for a source; return received/stored/quarantined.

    Counts are per HSDS object; a record that could not be mapped at all counts as
    one received, quarantined item. So received == stored + quarantined always.
    """
    result = run(adapter, store)
    unmappable = result.get("records_quarantined", 0)
    counts = IngestCounts(
        received=result.get("objects", 0) + unmappable,
        stored=result.get("kept", 0),
        quarantined=result.get("quarantined", 0) + unmappable,
    )
    assert counts.is_reconciled(), counts
    return counts
