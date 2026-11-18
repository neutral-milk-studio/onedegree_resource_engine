"""Search query — STUB (P5 stretch, task 30.3/30.5).

Keyword search via the index; semantic ranking via Bedrock embeddings on top.
Results are HSDS. Spec: Requirements 34, 35, 37, 38.
"""

from __future__ import annotations

from typing import Any


def search_services(query: str) -> list[dict[str, Any]]:
    """Return HSDS service records matching the query (keyword; semantic on top)."""
    raise NotImplementedError("task 30.3: keyword search; task 30.5: semantic on top")
