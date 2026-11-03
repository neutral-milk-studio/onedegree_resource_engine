"""Findings store — STUB (task 21).

Persists Finding + Evidence in ops.* tables SEPARATE from the HSDS store. Rejects
any proposal lacking a non-empty evidence snippet. Spec: Requirements 23, 24.
"""

from __future__ import annotations

from contracts.findings import Finding


class PgFindingsStore:
    def save(self, finding: Finding) -> str:
        """Persist a finding; refuse to store an evidence-free discrepancy proposal."""
        raise NotImplementedError("task 21.1: persist findings in ops schema, require evidence")
