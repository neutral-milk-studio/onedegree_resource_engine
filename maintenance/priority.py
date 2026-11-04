"""Harm-based priority. Lower rank = more harmful = reviewed first.

actively_bad > suspicious > discrepancy in phone/hours/address > other discrepancy
> missing_information > check_failed
"""

from __future__ import annotations

CONTACT_FIELDS = {"phone", "hours", "address"}

RANKS = {
    "actively_bad": 0,
    "suspicious": 1,
    "discrepancy_contact": 2,
    "discrepancy": 3,
    "missing_information": 4,
    "check_failed": 5,
    "verified": 9,
}


def harm_rank(category: str, field: str | None) -> int:
    if category == "discrepancy" and field in CONTACT_FIELDS:
        return RANKS["discrepancy_contact"]
    return RANKS.get(category, 8)


def harm_label(rank: int) -> str:
    return {
        0: "Urgent: people may be sent to a closed or dead service",
        1: "Urgent: possible scam, flagged for review",
        2: "High: people may call or arrive at the wrong time or number",
        3: "Medium: a stated fact differs",
        4: "Low: information is missing",
        5: "Low: the website couldn't be checked",
    }.get(rank, "Info")
