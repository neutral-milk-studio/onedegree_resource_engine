"""Raw store factory and the S3 implementation: real S3 wire behavior (via LocalStack),
not mocks, so a real serialization or signing bug would actually be caught.

Needs a running LocalStack: ``docker compose --profile full up -d localstack`` and a
bucket named ``RE_RAW_BUCKET`` (see Makefile target ``localstack-bucket``). Skipped
automatically if LocalStack isn't reachable, so the default `make test` run (no AWS
services) is unaffected.
"""

from __future__ import annotations

import os

import pytest

from ingest.raw_store import get_raw_store
from ingest.raw_store.local import LocalRawStore
from ingest.raw_store.s3_raw_store import S3RawStore, raw_bucket

LOCALSTACK = os.environ.get("RE_S3_ENDPOINT", "http://localhost:4566")
BUCKET = "resource-engine-raw-test"


def _localstack_client():
    boto3 = pytest.importorskip("boto3")
    from botocore.config import Config

    try:
        client = boto3.client(
            "s3",
            endpoint_url=LOCALSTACK,
            region_name="us-west-2",
            aws_access_key_id="test",
            aws_secret_access_key="test",
            # Fail fast when LocalStack isn't running, so the rest of the suite (which
            # doesn't need AWS services) isn't slowed down waiting on this check.
            config=Config(connect_timeout=1, read_timeout=1, retries={"max_attempts": 0}),
        )
        client.list_buckets()
    except Exception as exc:  # noqa: BLE001 - LocalStack not running: skip, don't fail
        pytest.skip(f"LocalStack not reachable at {LOCALSTACK}: {exc}")
    return client


@pytest.fixture
def s3_client():
    client = _localstack_client()
    client.create_bucket(
        Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "us-west-2"}
    )
    yield client
    objects = client.list_objects_v2(Bucket=BUCKET).get("Contents", [])
    for obj in objects:
        client.delete_object(Bucket=BUCKET, Key=obj["Key"])
    client.delete_bucket(Bucket=BUCKET)


@pytest.fixture
def s3_store(s3_client, monkeypatch):
    monkeypatch.setenv("RE_S3_ENDPOINT", LOCALSTACK)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    return S3RawStore(bucket=BUCKET, endpoint_url=LOCALSTACK)


def test_round_trips_content_byte_for_byte(s3_store):
    content = b'{"opportunities": [{"id": 1}]}\n\xff\x00binary-safe'
    ref = s3_store.write("odrs", "2026-11-04T12:00:00Z", "page.json", content)
    assert s3_store.read(ref) == content


def test_key_layout_matches_the_local_store(s3_store):
    # Colons stripped from the timestamp, same as LocalRawStore — filesystem- and
    # S3-key-safe either way.
    ref = s3_store.write("ndp", "2026-11-04T05:09:00+00:00", "national-data-1.json", b"{}")
    assert ref == "ndp/2026-11-04T050900+0000/national-data-1.json"


def test_bucket_name_from_either_env_var(monkeypatch):
    monkeypatch.delenv("RE_RAW_BUCKET", raising=False)
    monkeypatch.delenv("S3_BUCKET", raising=False)
    assert raw_bucket() is None
    monkeypatch.setenv("S3_BUCKET", "from-terraform")
    assert raw_bucket() == "from-terraform"
    monkeypatch.setenv("RE_RAW_BUCKET", "from-dotenv")
    assert raw_bucket() == "from-dotenv"  # this project's own name wins if both are set


def test_missing_bucket_raises_a_clear_error(monkeypatch):
    monkeypatch.delenv("RE_RAW_BUCKET", raising=False)
    monkeypatch.delenv("S3_BUCKET", raising=False)
    with pytest.raises(RuntimeError, match="RE_RAW_BUCKET or S3_BUCKET"):
        S3RawStore()


class TestFactory:
    def test_defaults_to_local_with_no_bucket_configured(self, monkeypatch):
        monkeypatch.delenv("RE_RAW_STORE", raising=False)
        monkeypatch.delenv("RE_RAW_BUCKET", raising=False)
        monkeypatch.delenv("S3_BUCKET", raising=False)
        assert isinstance(get_raw_store(), LocalRawStore)

    def test_picks_s3_when_a_bucket_is_configured(self, monkeypatch):
        monkeypatch.delenv("RE_RAW_STORE", raising=False)
        monkeypatch.setenv("S3_BUCKET", "whatever")
        assert isinstance(get_raw_store(), S3RawStore)

    def test_re_raw_store_overrides_explicitly(self, monkeypatch):
        monkeypatch.setenv("S3_BUCKET", "whatever")
        monkeypatch.setenv("RE_RAW_STORE", "local")
        assert isinstance(get_raw_store(), LocalRawStore)
        monkeypatch.delenv("S3_BUCKET")
        monkeypatch.setenv("RE_RAW_STORE", "s3")
        with pytest.raises(RuntimeError, match="bucket"):
            get_raw_store()  # explicit s3, but no bucket configured: fails loudly


def test_full_ingest_writes_raw_payload_to_real_s3(s3_store, s3_client, store):
    """End-to-end: the pipeline's default raw store (the factory) really hits S3."""
    import json

    from ingest.adapters import odrs
    from ingest.adapters.base import ModuleAdapter, RawPage
    from ingest.pipeline.run import run_ingest

    page = {"opportunities": [{"id": 1, "title": "T", "organization": {"id": 2, "name": "O"}}]}
    adapter = ModuleAdapter(odrs, inputs=[])
    adapter.fetch_pages = lambda since=None: iter([RawPage("p.json", json.dumps(page).encode())])
    report = run_ingest(adapter, store, s3_store, triggered_by="test")
    assert report.received == 1 and report.stored == 1
    obj = s3_client.get_object(Bucket=BUCKET, Key=report.raw_refs[0])
    assert obj["Body"].read() == json.dumps(page).encode()
