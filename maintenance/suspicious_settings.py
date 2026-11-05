"""Suspicious-listing detection settings: every weight and the flag threshold, in one
place, each overridable by an environment variable so a deploy can tune it without a
code change.

Score is the sum of the weights of the signals a page trips (capped at 100); a listing is
flagged ("suspicious") once its score reaches ``THRESHOLD``. Below that, the signals are
still recorded on the check (for visibility) but no finding is created.
"""

from __future__ import annotations

import os

THRESHOLD = int(os.environ.get("RE_SUSPICIOUS_THRESHOLD", "40"))

# Strong: on their own, a reasonable person would call these a red flag.
WEIGHT_FEE_BEFORE_HELP = int(os.environ.get("RE_SUSPICIOUS_W_FEE_BEFORE_HELP", "40"))
WEIGHT_SENSITIVE_DATA_REQUEST = int(os.environ.get("RE_SUSPICIOUS_W_SENSITIVE_DATA_REQUEST", "40"))

# Medium: suspicious together with anything else, not damning alone.
WEIGHT_URGENCY_PRESSURE = int(os.environ.get("RE_SUSPICIOUS_W_URGENCY_PRESSURE", "20"))
WEIGHT_FAKE_OFFICIAL = int(os.environ.get("RE_SUSPICIOUS_W_FAKE_OFFICIAL", "20"))
WEIGHT_FEE_MISMATCH = int(os.environ.get("RE_SUSPICIOUS_W_FEE_MISMATCH", "20"))

# Weak: context, not evidence.
WEIGHT_DOMAIN_MISMATCH = int(os.environ.get("RE_SUSPICIOUS_W_DOMAIN_MISMATCH", "10"))
WEIGHT_NEW_DOMAIN = int(os.environ.get("RE_SUSPICIOUS_W_NEW_DOMAIN", "10"))
WEIGHT_NOT_IN_REGISTRY = int(os.environ.get("RE_SUSPICIOUS_W_NOT_IN_REGISTRY", "10"))
WEIGHT_MESSAGING_ONLY_CONTACT = int(os.environ.get("RE_SUSPICIOUS_W_MESSAGING_ONLY_CONTACT", "10"))

NEW_DOMAIN_DAYS = int(os.environ.get("RE_NEW_DOMAIN_DAYS", "90"))
RDAP_TIMEOUT_S = float(os.environ.get("RE_SUSPICIOUS_RDAP_TIMEOUT_S", "3"))
