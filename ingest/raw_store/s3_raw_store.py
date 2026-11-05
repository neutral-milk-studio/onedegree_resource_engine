"""S3 raw store: each fetched payload is written byte-for-byte to S3 BEFORE any processing.

Same key layout and interface as ``local.py`` (``<source>/<fetched_at>/<name>``), so the
pipeline and tests don't need to know which one is running — only
``ingest/raw_store/__init__.py``'s factory picks between them, by environment.
Encrypted at rest via the bucket's default SSE (set by Terraform:
``aws_s3_bucket_server_side_encryption_configuration``, not by this module).
"""

from __future__ import annotations

import os
from functools import cached_property
from pathlib import Path


def raw_bucket() -> str | None:
    """The configured raw-store bucket, under either naming convention.

    ``RE_RAW_BUCKET`` is this project's own name (``.env.example``); ``S3_BUCKET`` is
    what the AWS deploy's ECS task gets from Terraform (``infra/terraform/compute.tf``).
    """
    return os.environ.get("RE_RAW_BUCKET") or os.environ.get("S3_BUCKET")


class S3RawStore:
    """Writes to S3 (or any S3-compatible endpoint, e.g. LocalStack for local testing)."""

    def __init__(self, bucket: str | None = None, endpoint_url: str | None = None) -> None:
        self.bucket = bucket or raw_bucket()
        if not self.bucket:
            raise RuntimeError("No raw-store bucket configured: set RE_RAW_BUCKET or S3_BUCKET.")
        self.endpoint_url = endpoint_url or os.environ.get("RE_S3_ENDPOINT") or None

    @cached_property
    def client(self):
        import boto3

        return boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            region_name=os.environ.get("AWS_REGION", "us-west-2"),
        )

    def write(self, source: str, fetched_at: str, name: str, content: bytes) -> str:
        """Write ``content`` unchanged and return its reference (the S3 key)."""
        safe_stamp = fetched_at.replace(":", "")
        key = (Path(source) / safe_stamp / Path(name).name).as_posix()
        self.client.put_object(Bucket=self.bucket, Key=key, Body=content)
        return key

    def read(self, ref: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=ref)["Body"].read()
