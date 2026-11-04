"""HSDS validation against the pinned v3.2.3 JSON Schema.

Used at every boundary: transformer output before storage, API responses, exports,
and every review save. A record that fails is quarantined or rejected with the
reported reason.

On top of the schema, this module enforces rules the schema allows through: empty
strings, and opening hours without a UTC offset.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "contracts" / "hsds" / "schema"
HSDS_VERSION = "3.2.3"
EXTENSION_KEY = "x_resource_engine"

_TIME_WITH_OFFSET = re.compile(r"^([01]\d|2[0-3]):[0-5]\d(Z|[+-]([01]\d|2[0-3]):[0-5]\d)$")


class SchemaNotAvailable(RuntimeError):
    """Raised when the pinned schema files are missing."""


@dataclass(frozen=True)
class Issue:
    """One validation problem. ``path`` uses ``/`` between keys and list indexes."""

    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path or '<root>'}: {self.message}"


@cache
def _registry() -> Registry:
    """Register every vendored schema so references like ``organization.json`` resolve."""
    paths = sorted(SCHEMA_DIR.glob("*.json"))
    if not paths:
        raise SchemaNotAvailable(f"No HSDS schema files found in {SCHEMA_DIR}.")
    resources = [
        (
            path.name,
            Resource.from_contents(
                json.loads(path.read_text(encoding="utf-8")), default_specification=DRAFT202012
            ),
        )
        for path in paths
    ]
    return Registry().with_resources(resources)


@cache
def _validator(object_type: str) -> Draft202012Validator:
    path = SCHEMA_DIR / f"{object_type}.json"
    if not path.exists():
        raise SchemaNotAvailable(f"HSDS schema for {object_type!r} not found at {path}.")
    return Draft202012Validator(
        json.loads(path.read_text(encoding="utf-8")),
        registry=_registry(),
        # Format checks are off by default, which would let non-UUID ids through.
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )


def _strict_issues(value: Any, path: str = "") -> list[Issue]:
    issues: list[Issue] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == EXTENSION_KEY:
                continue
            child_path = f"{path}/{key}" if path else key
            if key in {"opens_at", "closes_at"} and isinstance(child, str):
                if not _TIME_WITH_OFFSET.fullmatch(child):
                    issues.append(Issue(child_path, f"{child!r} needs HH:MM and a UTC offset"))
            issues.extend(_strict_issues(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            issues.extend(_strict_issues(child, f"{path}/{index}"))
    elif value == "":
        issues.append(Issue(path, "omit empty values instead of sending an empty string"))
    return issues


def find_issues(obj: dict[str, Any], object_type: str) -> list[Issue]:
    """Return every problem with ``obj``. An empty list means it is valid."""
    errors = sorted(
        _validator(object_type).iter_errors(obj), key=lambda error: list(map(str, error.path))
    )
    schema_issues = [
        Issue("/".join(str(part) for part in error.absolute_path), error.message)
        for error in errors
    ]
    return schema_issues + _strict_issues(obj)


def validate(obj: dict[str, Any], object_type: str) -> list[str]:
    """Return readable error messages. An empty list means ``obj`` is valid."""
    return [str(issue) for issue in find_issues(obj, object_type)]


def is_valid(obj: dict[str, Any], object_type: str) -> bool:
    """Return True if ``obj`` is valid HSDS for the pinned version."""
    return not find_issues(obj, object_type)


def assert_valid(obj: dict[str, Any], object_type: str) -> None:
    """Raise ValueError if ``obj`` is not valid HSDS for the pinned version."""
    errors = validate(obj, object_type)
    if errors:
        raise ValueError(
            f"HSDS v{HSDS_VERSION} validation failed for {object_type}: " + "; ".join(errors)
        )
