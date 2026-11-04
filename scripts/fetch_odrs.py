"""Pull ODRS (One Degree) data from the staging API and save it exactly as received.

Setup (once): create a file named .env in the repo root containing
    ODRS_API_KEY=<staging key from extra_context.md>
.env is gitignored. Never put the key in code or commit it.

Usage (from the repo root):
    python scripts/fetch_odrs.py                      # opportunities + organizations
    python scripts/fetch_odrs.py --resource opportunities
    python scripts/fetch_odrs.py --max-pages 2        # quick test
    python scripts/fetch_odrs.py --out full           # full dump (data/raw/odrs/full/ is gitignored)
    python scripts/fetch_odrs.py --per-page 100       # ask for bigger pages

Output: data/raw/odrs/<--out, default UTC fetch time>/<resource>_page<NNN>.json, one file
per page, plus manifest.json with counts. If --max-pages stops a resource early, the
manifest marks it "truncated": true with the next_page to resume from. Re-running into the
same --out folder (e.g. one --resource at a time) merges into the existing manifest. The api_key is scrubbed from any URLs saved in the files.
Standard library only; no pip installs needed.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HOST = "https://rbg.1degree.org:3002"
USER_AGENT = "ResourceEngine-Hackathon/0.1 (One Degree hackathon team)"
SECRET_PARAM = re.compile(r"([?&](?:amp;)?)(api_key|apikey|token|access_token)=[^&\"\s]*", re.I)
# (resource path, key holding the record list in the response)
RESOURCES = {"opportunities": "opportunities", "organizations": "organizations"}


def load_env() -> None:
    """Read KEY=VALUE lines from .env into os.environ (without overriding real env vars)."""
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def with_key(url: str, key: str) -> str:
    parts = urllib.parse.urlsplit(url)
    query = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if k != "api_key"
    ]
    query.append(("api_key", key))
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def scrub(text: str) -> str:
    return SECRET_PARAM.sub(r"\1\2=REDACTED", text)


def get(url: str, key: str) -> str:
    req = urllib.request.Request(
        with_key(url, key), headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


def fetch_resource(
    host: str, resource: str, key: str, out_dir: Path, max_pages: int | None, per_page: int | None
) -> dict:
    url = f"{host}/v1/{resource}"
    if per_page:
        url += f"?per_page={per_page}"
    page, records, total, per_page_reported = 1, 0, None, None
    while url and (max_pages is None or page <= max_pages):
        print(f"  {resource} page {page} ...", flush=True)
        body = get(url, key)
        (out_dir / f"{resource}_page{page:03d}.json").write_text(scrub(body))
        data = json.loads(body)
        items = data.get(RESOURCES[resource], [])
        records += len(items) if isinstance(items, list) else 0
        paging = data.get("paging") or {}
        total = paging.get("total_count", total)
        per_page_reported = paging.get("per_page", per_page_reported)
        url = paging.get("next_page")
        if url:
            # The API's next_page omits the staging port (:3002), and that host rejects the
            # staging key, so keep only its path + query and always use the configured host.
            nxt = urllib.parse.urlsplit(url)
            url = host + nxt.path + (f"?{nxt.query}" if nxt.query else "")
        page += 1
        if url:
            time.sleep(0.5)  # be polite to the server
    result = {
        "pages": page - 1,
        "records_saved": records,
        "total_count_reported": total,
        "per_page_reported": per_page_reported,
        "truncated": bool(url),
    }
    if url:
        result["next_page"] = scrub(url)
        print(
            f"  WARNING: {resource} stopped at --max-pages {max_pages} with more pages remaining "
            f"({records} of {total} records saved). Resume from: {scrub(url)}",
            file=sys.stderr,
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--resource", choices=["all", *RESOURCES], default="all")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="stop after N pages per resource (default: no limit)",
    )
    parser.add_argument(
        "--out", help="output folder name under data/raw/odrs/ (default: UTC fetch time)"
    )
    parser.add_argument(
        "--per-page", type=int, help="request this many records per page (sent as per_page=<n>)"
    )
    args = parser.parse_args()

    load_env()
    key = os.environ.get("ODRS_API_KEY")
    if not key:
        print(
            "ODRS_API_KEY not set. Add it to .env in the repo root (see the top of this file).",
            file=sys.stderr,
        )
        return 1
    host = os.environ.get("ODRS_HOST", DEFAULT_HOST).rstrip("/")

    fetched_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_name = args.out or fetched_at
    if Path(out_name).name != out_name or out_name in ("", ".", ".."):
        print(f"--out must be a plain folder name, got {out_name!r}", file=sys.stderr)
        return 1
    out_dir = REPO_ROOT / "data" / "raw" / "odrs" / out_name
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest_file = out_dir / "manifest.json"
    manifest = (
        json.loads(manifest_file.read_text())
        if manifest_file.exists()
        else {"source": "odrs", "resources": {}}
    )
    manifest.update({"host": host, "fetched_at": fetched_at})
    for resource in RESOURCES if args.resource == "all" else [args.resource]:
        try:
            manifest["resources"][resource] = fetch_resource(
                host, resource, key, out_dir, args.max_pages, args.per_page
            )
            manifest["resources"][resource]["fetched_at"] = fetched_at
        except urllib.error.HTTPError as exc:
            print(f"HTTP {exc.code} on {resource}: check the key and host.", file=sys.stderr)
            manifest["resources"][resource] = {"error": f"HTTP {exc.code}"}
        except Exception as exc:  # report, don't hide
            print(f"Failed on {resource}: {exc}", file=sys.stderr)
            manifest["resources"][resource] = {"error": str(exc)}
    manifest_file.write_text(json.dumps(manifest, indent=2))
    print(f"Saved to {out_dir.relative_to(REPO_ROOT)}")
    print(json.dumps(manifest["resources"], indent=2))
    return 0 if all("error" not in r for r in manifest["resources"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
