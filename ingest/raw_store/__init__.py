"""Raw store factory: local folder by default, S3 when a bucket is configured.

Both implementations satisfy the same ``write(source, fetched_at, name, content) -> ref``
/ ``read(ref) -> bytes`` interface, so ``ingest.pipeline.run`` never checks which one is
running. Selected by environment, not by code change, so a hackathon laptop run and the
AWS deploy (``infra/terraform``) use the same pipeline code unmodified.
"""

from __future__ import annotations

import os


def get_raw_store():
    """``RE_RAW_STORE`` ("local" | "s3") picks explicitly; otherwise S3 is used if a
    bucket is configured (``RE_RAW_BUCKET`` or the AWS deploy's ``S3_BUCKET``), else local."""
    from ingest.raw_store.local import LocalRawStore
    from ingest.raw_store.s3_raw_store import S3RawStore, raw_bucket

    choice = os.environ.get("RE_RAW_STORE", "").strip().lower()
    if choice == "local":
        return LocalRawStore()
    if choice == "s3":
        return S3RawStore()
    return S3RawStore() if raw_bucket() else LocalRawStore()
