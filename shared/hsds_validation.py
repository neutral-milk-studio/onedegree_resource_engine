"""HSDS validation against the pinned v3.2.3 JSON Schema.

Used at every boundary: transformer output before storage, API responses (in
tests), any export, and every admin-UI save. A record that fails is quarantined
with the reported reason.

Spec: Requirements 11.2, 43.1, 43.2.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

# Directory holding the vendored Open Referral JSON Schema files for the pinned
# HSDS version (see contracts/hsds/README.md).
SCHEMA_DIR = Path(__file__).resolve().parent.parent / "contracts" / "hsds" / "schema"

HSDS_VERSION = "3.2.3"


class SchemaNotAvailable(RuntimeError):
    """Raised when the pinned schema files have not been vendored yet."""


@lru_cache(maxsize=None)
def _load_schema(object_type: str) -> dict[str, Any]:
    """Load the JSON Schema for one HSDS object type (e.g. 'service').

    The schema files are the authoritative source. They are vendored under
    ``contracts/hsds/schema/`` from the pinned specification tag.
    """
    path = SCHEMA_DIR / f"{object_type}.json"
    if not path.exists():
        raise SchemaNotAvailable(
            f"HSDS schema for {object_type!r} not found at {path}. "
            "Vendor the v3.2.3 schema files into contracts/hsds/schema/ "
            "(see contracts/hsds/README.md)."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def validate(obj: dict[str, Any], object_type: str) -> list[str]:
    """Validate one HSDS object against the pinned schema.

    Returns a list of human-readable error messages; an empty list means valid.
    Uses ``jsonschema`` if available; raises :class:`SchemaNotAvailable` if the
    schema files have not been vendored.

    Parameters
    ----------
    obj:
        The HSDS object to validate.
    object_type:
        The HSDS object type name, matching a vendored schema file
        (e.g. "organization", "service", "location").
    """
    schema = _load_schema(object_type)

    try:
        import jsonschema  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "jsonschema is required for HSDS validation; add it to the service "
            "dependencies."
        ) from exc

    validator = jsonschema.Draft7Validator(schema)
    return [
        f"{'/'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in sorted(validator.iter_errors(obj), key=lambda e: list(e.absolute_path))
    ]


def is_valid(obj: dict[str, Any], object_type: str) -> bool:
    """True iff ``obj`` is valid HSDS for the pinned version."""
    return not validate(obj, object_type)


def assert_valid(obj: dict[str, Any], object_type: str) -> None:
    """Raise ValueError if ``obj`` is not valid HSDS for the pinned version."""
    errors = validate(obj, object_type)
    if errors:
        raise ValueError(
            f"HSDS v{HSDS_VERSION} validation failed for {object_type}: "
            + "; ".join(errors)
        )
