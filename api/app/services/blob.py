"""Blob storage for raw payloads and uploaded documents: local disk or S3."""

from __future__ import annotations

from pathlib import Path


class LocalBlobStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def put(self, key: str, data: bytes) -> str:
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        return (self.root / key).read_bytes()


class S3BlobStore:  # pragma: no cover - needs AWS
    def __init__(self, bucket: str, region: str):
        import boto3

        self.bucket, self.s3 = bucket, boto3.client("s3", region_name=region)

    def put(self, key: str, data: bytes) -> str:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ServerSideEncryption="AES256")
        return key

    def get(self, key: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()


def build_blob_store(settings):
    if settings.blob_backend == "s3":
        return S3BlobStore(settings.s3_bucket, settings.aws_region)
    return LocalBlobStore(settings.data_dir / "blobs")
