"""Partner sources added through the dashboard: preview (suggest a mapping) and confirm."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

from api.app.store_pg import PgHsdsStore
from ingest.adapters.registry import source_names
from ingest.adapters.spreadsheet import TARGETS, SpreadsheetAdapter, read_csv, slug
from ingest.mapping import suggest_mapping
from shared.models import AuditEntry, SourceMapping, SourceUpload

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000


class PartnerError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


def source_key(partner_name: str) -> str:
    key = f"partner_{slug(partner_name)}"[:64].rstrip("-")
    if key == "partner_":
        raise PartnerError(422, "Give the partner a name with letters or numbers.")
    return key


def preview(store: PgHsdsStore, filename: str, content: bytes, actor: str, client_factory=None):
    if not content:
        raise PartnerError(422, "The file is empty.")
    if len(content) > MAX_BYTES:
        raise PartnerError(413, "The file is larger than 2 MB. Split it and upload the parts.")
    if not re.search(r"\.(csv|txt)$", filename or "", re.I):
        raise PartnerError(
            415, "Upload a CSV file. In Excel or Google Sheets: File → Download → CSV."
        )
    try:
        headers, rows = read_csv(content)
    except ValueError as exc:
        raise PartnerError(422, str(exc)) from exc
    if not rows:
        raise PartnerError(422, "The file has a header row but no data rows.")
    if len(rows) > MAX_ROWS:
        raise PartnerError(413, f"The file has {len(rows)} rows; the limit is {MAX_ROWS}.")
    suggestion = suggest_mapping(headers, rows, client_factory=client_factory)
    with store.session() as session:
        upload = SourceUpload(
            filename=filename,
            content=content,
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            rows=len(rows),
            suggestion=suggestion.as_dict(),
            uploaded_by=actor,
        )
        session.add(upload)
        session.flush()
        return upload.id, len(rows), suggestion


def confirm(
    store: PgHsdsStore,
    upload_id: str,
    partner_name: str,
    mapping: dict[str, str],
    actor: str,
    default_state: str = "CA",
) -> tuple[str, SpreadsheetAdapter]:
    """Save the confirmed mapping and return the source key and its adapter (to ingest)."""
    partner_name = (partner_name or "").strip()
    if not partner_name:
        raise PartnerError(422, "Give the partner a name.")
    key = source_key(partner_name)
    if key in source_names():
        raise PartnerError(409, f"“{key}” is already a configured source.")
    unknown = sorted({t for t in mapping.values() if t not in TARGETS})
    if unknown:
        raise PartnerError(422, f"Unknown field(s): {', '.join(unknown)}.")
    if not {"organization_name", "service_name"} & set(mapping.values()):
        raise PartnerError(422, "Map at least one column to Organization name or Service name.")
    if not re.fullmatch(r"[A-Z]{2}", default_state or ""):
        raise PartnerError(422, "The default state must be two capital letters, such as CA.")
    with store.session() as session:
        upload = session.get(SourceUpload, upload_id)
        if upload is None:
            raise PartnerError(404, "That upload wasn't found. Upload the file again.")
        headers, _ = read_csv(upload.content)
        missing = sorted(set(headers) - set(mapping))
        extra = sorted(set(mapping) - set(headers))
        if missing or extra:
            raise PartnerError(
                422,
                f"The mapping must cover every column. Missing: {missing}; "
                f"not in the file: {extra}.",
            )
        row = session.get(SourceMapping, key)
        now = datetime.now(UTC)
        if row is None:
            row = SourceMapping(source=key)
            session.add(row)
        row.partner_name, row.upload_id, row.mapping = partner_name, upload_id, mapping
        row.suggested, row.default_state = upload.suggestion, default_state
        row.confirmed_by, row.confirmed_at = actor, now
        changed = {
            c["column"]: {"suggested": c["target"], "chosen": mapping[c["column"]]}
            for c in upload.suggestion["columns"]
            if mapping.get(c["column"]) != c["target"]
        }
        session.add(
            AuditEntry(
                actor=actor, action="partner_mapping_confirmed", change_source="specialist",
                entity_type="source", entity_id=key,
                new_value={"mapping": mapping, "changed": changed},
                at=now,
                summary=(f"{actor} added partner “{partner_name}” "
                         f"({upload.filename}, {upload.rows} rows"
                         + (f", {len(changed)} suggestion(s) changed" if changed else "") + ")"),
            )
        )  # fmt: skip
        adapter = SpreadsheetAdapter(key, upload.filename, upload.content, mapping, default_state)
    return key, adapter


def partner_adapter(store: PgHsdsStore, source: str) -> SpreadsheetAdapter | None:
    """The adapter for a confirmed partner source (its last confirmed upload), or None."""
    with store.session() as session:
        row = session.get(SourceMapping, source)
        if row is None:
            return None
        upload = session.get(SourceUpload, row.upload_id)
        return SpreadsheetAdapter(
            source, upload.filename, upload.content, row.mapping, row.default_state
        )
