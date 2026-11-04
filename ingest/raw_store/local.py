"""Local-folder raw store: each fetched payload is written byte-for-byte BEFORE any processing.

Key layout matches the S3 raw store (docs/decisions.md section 3):
``<source>/<fetched_at ISO8601>/<page name>``. Swap for ``s3_raw_store`` in AWS by setting
``RE_RAW_STORE=s3`` (not wired yet).
"""

from __future__ import annotations

import os
from pathlib import Path

from ingest.adapters.base import REPO_ROOT


def raw_root() -> Path:
    return Path(os.environ.get("RE_RAW_DIR", str(REPO_ROOT / "var" / "raw")))


class LocalRawStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or raw_root()

    def write(self, source: str, fetched_at: str, name: str, content: bytes) -> str:
        """Write ``content`` unchanged and return its reference (path relative to the root)."""
        safe_stamp = fetched_at.replace(":", "")
        key = Path(source) / safe_stamp / Path(name).name
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return key.as_posix()

    def read(self, ref: str) -> bytes:
        return (self.root / ref).read_bytes()
