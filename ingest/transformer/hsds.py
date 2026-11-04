"""HSDS rulebook and shared helpers (ported unchanged from feature/ingest-transform).

Validates objects against the vendored Open Referral HSDS v3.2.3 JSON Schemas
(contracts/hsds/schema/, copied from github.com/openreferral/specification@v3.2.3)
and provides the small helpers every source mapper uses.
"""

import json
import re
import uuid
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

HSDS_VERSION = "3.2.3"
SCHEMA_DIR = Path(__file__).resolve().parents[2] / "contracts" / "hsds" / "schema"
# Tabular HSDS definition (datapackage.json) from the same v3.2.3 release. Kept beside this
# module rather than in contracts/ so the protected, unedited schema folder stays as vendored.
DATAPACKAGE = Path(__file__).resolve().parent / "hsds_datapackage.json"

# Fixed namespace for deterministic ids. Never change it: every stored id derives from it.
NAMESPACE = uuid.UUID("6f1c2a52-6b0e-4a5e-9d3a-2f3b7d0c9e41")

# The schema files declare no $schema and reference each other by bare filename
# ("$ref": "phone.json"), so register every file under its filename.
_schemas = {p.name: json.loads(p.read_text()) for p in sorted(SCHEMA_DIR.glob("*.json"))}
_registry = Registry().with_resources(
    (name, Resource.from_contents(schema, default_specification=DRAFT202012))
    for name, schema in _schemas.items()
)
# FormatChecker makes format: uuid / email / uri actually enforced (uri needs rfc3987).
_validators = {
    name.removesuffix(".json"): Draft202012Validator(
        schema, registry=_registry, format_checker=FormatChecker()
    )
    for name, schema in _schemas.items()
}

KINDS = frozenset(_validators)

# Tabular form of HSDS (one table per object type): datapackage.json from the same release.
_package = json.loads(DATAPACKAGE.read_text())
TABLES: dict[str, dict] = {r["name"]: r["schema"] for r in _package["resources"]}
COLUMNS: dict[str, list[str]] = {t: [f["name"] for f in s["fields"]] for t, s in TABLES.items()}
# kind -> [(field, parent kind)], e.g. phone -> [("service_id", "service"), ...]
FOREIGN_KEYS: dict[str, list[tuple[str, str]]] = {
    t: [(fk["fields"], fk["reference"]["resource"]) for fk in s.get("foreignKeys", [])]
    for t, s in TABLES.items()
}
# Fields that are optional in JSON but required once stored as a table row
# (e.g. service.organization_id). Databases without constraints won't check these, so we do.
TABULAR_REQUIRED: dict[str, list[str]] = {
    name.removesuffix(".json"): s.get("tabular_required", []) for name, s in _schemas.items()
}


# Resource Engine data-quality rules: checks HSDS itself doesn't make, for values that are
# valid HSDS but plainly wrong. Kept short and labelled so they're never mistaken for HSDS.
QUALITY_RULES: dict[str, list[tuple[str, callable, str]]] = {
    "phone": [("number", lambda v: any(c.isdigit() for c in v), "contains no digits")],
}


def validate(kind: str, obj: dict) -> list[str]:
    """Return a list of validation errors; empty means valid HSDS, storable as a table row."""
    if kind not in _validators:
        return [f"(root): unknown HSDS object kind {kind!r}"]
    errors = [
        f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
        for e in _validators[kind].iter_errors(obj)
    ]
    errors += [
        f"(root): {field!r} is required when stored (tabular_required)"
        for field in TABULAR_REQUIRED.get(kind, [])
        if field not in obj
    ]
    errors += [
        f"{field}: {obj[field]!r} {message} (Resource Engine quality rule)"
        for field, check, message in QUALITY_RULES.get(kind, [])
        if isinstance(obj.get(field), str) and not check(obj[field])
    ]
    return sorted(errors)


def parents(kind: str, obj: dict) -> list[tuple[str, str]]:
    """The (parent kind, parent id) pairs this object points at."""
    out = [(parent, obj[field]) for field, parent in FOREIGN_KEYS.get(kind, []) if obj.get(field)]
    if kind == "attribute" and obj.get("link_id") and obj.get("link_entity"):
        out.append((obj["link_entity"], obj["link_id"]))  # attribute links to any entity by link_id
    return out


def hsds_id(source: str, kind: str, native_id) -> str:
    """Deterministic UUIDv5, so re-ingesting a record updates it instead of duplicating it."""
    return str(uuid.uuid5(NAMESPACE, f"{source}:{kind}:{native_id}"))


def source_tag(source: str, native_id, **extra) -> dict:
    """The x_resource_engine extension carrying provenance (section 4.5)."""
    return {
        "x_resource_engine": {"source": {"system": source, "record_id": str(native_id)}, **extra}
    }


_EMPTY = (None, "", [], {})


def clean(obj):
    """Recursively drop None / "" / [] / {} values (section 4.4: omit rather than send empty)."""
    if isinstance(obj, dict):
        cleaned = ((k, clean(v)) for k, v in obj.items())
        return {k: v for k, v in cleaned if v not in _EMPTY}
    if isinstance(obj, list):
        return [v for v in (clean(v) for v in obj) if v not in _EMPTY]
    if isinstance(obj, str):
        return obj.strip()
    return obj


def strip_html(text: str | None) -> str | None:
    if not text:
        return text
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def to_number(value):
    """'18' -> 18; anything that does not parse cleanly -> None."""
    if value is None or isinstance(value, (int, float)):
        return value
    try:
        return int(str(value).strip())
    except ValueError:
        return None


_EXTENSION = re.compile(r"^(?P<number>.*?)\s*(?:x|ext\.?|extension)\s*(?P<ext>\d+)\s*$", re.I)


def split_phone(raw: str) -> tuple[str, int | None]:
    """'650-276-4101 x 4' -> ('650-276-4101', 4). HSDS phone.extension is a number."""
    m = _EXTENSION.match(raw or "")
    if not m:
        return (raw or "").strip(), None
    return m["number"].strip(), int(m["ext"])
