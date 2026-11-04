"""Trust score per record (0-100) and the plain-language badge shown with it.

- Not checked yet: no score.
- Verified recently: high, slowly decaying with age (a check goes stale).
- Each open finding lowers it by how harmful it is; a failed check lowers it too.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

PENALTY = {0: 60, 1: 60, 2: 25, 3: 15, 4: 5, 5: 15}  # by harm rank
BASE = {
    "verified": 100,
    "discrepancy": 90,
    "missing_information": 90,
    "check_failed": 60,
    "actively_bad": 40,
    "suspicious": 40,
}


@dataclass(frozen=True)
class Trust:
    score: int | None
    level: str  # high | medium | low | unknown
    badge: str
    checked_at: datetime | None


def _ago(when: datetime, now: datetime) -> str:
    days = (now - when).days
    if days <= 0:
        return "today"
    return "yesterday" if days == 1 else f"{days} days ago"


def trust_for(
    last_check_category: str | None,
    last_checked_at: datetime | None,
    open_harm_ranks: list[int],
    now: datetime | None = None,
) -> Trust:
    now = now or datetime.now(UTC)
    if last_check_category is None or last_checked_at is None:
        return Trust(None, "unknown", "Not checked yet", None)
    if last_checked_at.tzinfo is None:
        last_checked_at = last_checked_at.replace(tzinfo=UTC)
    age_days = max(0, (now - last_checked_at).days)
    score = BASE.get(last_check_category, 70) - min(40, age_days // 3)
    score -= sum(PENALTY.get(rank, 5) for rank in open_harm_ranks)
    score = max(0, min(100, score))
    level = "high" if score >= 80 else "medium" if score >= 50 else "low"
    if open_harm_ranks and level == "high":
        level = "medium"  # never show a "trusted" badge while something is open

    ago = _ago(last_checked_at, now)
    if open_harm_ranks:
        n = len(open_harm_ranks)
        badge = f"{n} open issue{'s' if n != 1 else ''} · checked {ago}"
    elif last_check_category == "verified":
        badge = f"Verified {ago}"
    elif last_check_category == "check_failed":
        badge = f"Couldn't check · {ago}"
    else:
        badge = f"Reviewed · checked {ago}"
    return Trust(score, level, badge, last_checked_at)
