"""Precision/recall eval for the suspicious-listing rules (maintenance/risk.py).

Run with ``make eval-suspicious``. Reads ``data/eval/suspicious_cases.json``, runs each
case through ``assess()``/``score()``, and compares the flag decision (score >= THRESHOLD)
against the case's ``expect_flagged``. Exits non-zero (failing CI) if precision or recall
falls below ``RE_SUSPICIOUS_EVAL_MIN`` (default 1.0 -- every case in this small, curated set
is expected to pass).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from maintenance.risk import assess, score
from maintenance.suspicious_settings import THRESHOLD

CASES_FILE = Path(__file__).resolve().parents[1] / "data" / "eval" / "suspicious_cases.json"


def _domain_registered(case: dict, today: date) -> str | None:
    age_days = case.get("domain_age_days")
    if age_days is None:
        return None
    return (today - timedelta(days=age_days)).isoformat()


def run(cases_file: Path = CASES_FILE) -> tuple[int, int, int, list[dict]]:
    """Returns (true_positives, false_positives, false_negatives, mistakes)."""
    today = date.today()
    cases = json.loads(cases_file.read_text(encoding="utf-8"))["cases"]
    tp = fp = fn = 0
    mistakes: list[dict] = []
    for case in cases:
        signals = assess(
            case["doc"],
            case["page_text"],
            case["url"],
            _domain_registered(case, today),
            today=today,
        )
        flagged = score(signals) >= THRESHOLD
        expected = case["expect_flagged"]
        if flagged and expected:
            tp += 1
        elif flagged and not expected:
            fp += 1
            mistakes.append({**case, "got_score": score(signals)})
        elif not flagged and expected:
            fn += 1
            mistakes.append({**case, "got_score": score(signals)})
    return tp, fp, fn, mistakes


def main() -> int:
    min_score = float(os.environ.get("RE_SUSPICIOUS_EVAL_MIN", "1.0"))
    tp, fp, fn, mistakes = run()
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    print(f"Precision: {precision:.2f}  Recall: {recall:.2f}  (tp={tp} fp={fp} fn={fn})")
    for case in mistakes:
        print(
            f"  MISS {case['id']}: expected_flagged={case['expect_flagged']} "
            f"got_score={case['got_score']}"
        )
    if precision < min_score or recall < min_score:
        print(f"FAILED: below required precision/recall of {min_score:.2f}")
        return 1
    print("PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
