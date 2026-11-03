"""EventBridge Scheduler wiring — STUB (task 22).

Triggers recurring maintenance checks. The scheduler is MAINTENANCE-ONLY; ingest
is on demand and never scheduled. Spec: Requirements 18.1, 18.2, 18.3.
"""

from __future__ import annotations


def schedule_maintenance() -> None:
    """Configure the recurring trigger for maintenance runs (never ingest)."""
    raise NotImplementedError("task 22: wire EventBridge Scheduler for maintenance checks")
