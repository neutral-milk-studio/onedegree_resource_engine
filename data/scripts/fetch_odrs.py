"""Download all pages of ODRS opportunities and save each page as received.

Usage:
    export ODRS_API_KEY=...        # staging key from One Degree, if required (never commit it)
    python scripts/fetch_odrs.py    # follows next_page starting from the page-1 sample

Saves data/samples/ODRS/odrs_page<N>_<YYYYMMDD>.json, one file per page, untouched
except that credential-like query parameters are stripped from any URL strings.
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "samples" / "ODRS"
SEED_FILE = RAW_DIR / "BayesExport_20250729.json"
USER_AGENT = "ResourceEngine-Hackathon/0.1 (One Degree hackathon team)"
SECRET_PARAM = re.compile(
    r"([?&])(api_key|apikey|key|token|access_token|signature|X-Amz-[A-Za-z-]+)=[^&\"\s]*", re.I
)


def with_key(url: str) -> str:
    key = os.environ.get("ODRS_API_KEY")
    if not key:
        return url
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if k != "api_key"] + [("api_key", key)]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def scrub(text: str) -> str:
    """Remove credential-looking query params from every URL in the payload."""
    return SECRET_PARAM.sub(r"\1\2=REDACTED", text)


def fetch(url: str) -> str:
    req = urllib.request.Request(
        with_key(url), headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def main() -> int:
    seed = json.loads(SEED_FILE.read_text())
    next_url = seed["paging"].get("next_page")
    page = 2
    today = date.today().strftime("%Y%m%d")
    while next_url:
        print(f"Fetching page {page} ...")
        try:
            body = fetch(next_url)
        except Exception as exc:  # report, don't hide
            print(f"Failed on page {page}: {exc}", file=sys.stderr)
            print(
                "If this is 401/403, you need ODRS_API_KEY (staging key from One Degree).",
                file=sys.stderr,
            )
            return 1
        out = RAW_DIR / f"odrs_page{page}_{today}.json"
        out.write_text(scrub(body))
        data = json.loads(body)
        print(f"  saved {len(data.get('opportunities', []))} opportunities -> {out.name}")
        next_url = data.get("paging", {}).get("next_page")
        page += 1
        time.sleep(1)  # be polite to the API
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
