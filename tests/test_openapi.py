"""The committed API contract is generated from the real app, so the two can't drift."""

from pathlib import Path

import yaml
from openapi_spec_validator import validate

from contracts.export_openapi import OPENAPI_PATH, render


def test_contract_matches_the_app():
    assert Path(OPENAPI_PATH).read_text() == render(), "run: make openapi"


def test_contract_is_valid_and_covers_both_apis():
    spec = yaml.safe_load(render())
    validate(spec)
    assert "/hsds/services" in spec["paths"]
    assert "/api/admin/change-requests/{cid}/approve" in spec["paths"]
