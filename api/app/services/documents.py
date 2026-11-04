"""Policy and eligibility documents (PDF, Word, text, HTML). Stored in S3/disk, text extracted, chunked and
indexed so the chat agent can answer from them with citations. Uploads by providers wait for admin approval."""

from __future__ import annotations

import hashlib
import io
import re
import zipfile

from shared.db import Database, loads, new_id, now_iso

CHUNK_CHARS, OVERLAP = 900, 150


def extract_text(filename: str, data: bytes, settings=None) -> tuple[str, str]:
    """Return (text, note). note explains partial extraction (e.g. scanned PDF needing OCR)."""
    name = filename.lower()
    if name.endswith(".pdf"):
        from pypdf import PdfReader

        try:
            text = "\n".join(
                (p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages
            ).strip()
        except Exception as e:  # noqa: BLE001
            return "", f"pdf_unreadable: {e}"
        if text:
            return text, "ok"
        if (
            settings is not None and settings.blob_backend == "s3"
        ):  # scanned PDF -> Textract (AWS only)
            try:  # pragma: no cover - needs AWS
                import boto3

                tx = boto3.client("textract", region_name=settings.aws_region)
                r = tx.detect_document_text(Document={"Bytes": data})
                t = "\n".join(b["Text"] for b in r["Blocks"] if b["BlockType"] == "LINE")
                return t, "ocr_textract(first page only; use async API for long scans)"
            except Exception as e:  # noqa: BLE001
                return "", f"no_text_layer; textract_failed: {e}"
        return "", "no_text_layer: scanned PDF needs OCR (Textract in AWS mode)"
    if name.endswith(".docx"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                xml = z.read("word/document.xml").decode("utf8", "ignore")
            xml = re.sub(r"</w:p>", "\n", xml)
            return re.sub(r"<[^>]+>", "", xml).strip(), "ok"
        except Exception as e:  # noqa: BLE001
            return "", f"docx_unreadable: {e}"
    text = data.decode("utf8", "ignore")
    if name.endswith((".html", ".htm")):
        from ingest.pipeline.transform.hsds import strip_html as html_to_text

        text = html_to_text(text) or ""
    return text.strip(), "ok"


def chunk_text(text: str) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n(?=[A-Z0-9#])", text) if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if len(cur) + len(p) + 1 <= CHUNK_CHARS:
            cur = f"{cur}\n{p}".strip()
        else:
            if cur:
                chunks.append(cur)
            while len(p) > CHUNK_CHARS:
                chunks.append(p[:CHUNK_CHARS])
                p = p[CHUNK_CHARS - OVERLAP :]
            cur = p
    if cur:
        chunks.append(cur)
    return chunks


class DocumentService:
    def __init__(self, settings, db: Database, blob):
        self.s, self.db, self.blob = settings, db, blob

    def upload(
        self,
        *,
        filename: str,
        data: bytes,
        title: str,
        uploaded_by: str,
        org_id: str | None = None,
        service_id: str | None = None,
        visibility: str = "public",
        effective_date: str | None = None,
        auto_approve: bool = False,
        content_type: str | None = None,
    ) -> dict:
        did = new_id()
        key = self.blob.put(f"documents/{org_id or 'general'}/{did}/{filename}", data)
        text, note = extract_text(filename, data, self.s)
        chunks = chunk_text(text) if text else []
        status = "approved" if (auto_approve and chunks) else "pending"
        with self.db.tx() as t:
            t.run(
                "INSERT INTO document (id,org_id,service_id,title,filename,content_type,blob_key,visibility,status,effective_date,"
                "uploaded_by,text_hash,chunk_count,extraction,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    did,
                    org_id,
                    service_id,
                    title,
                    filename,
                    content_type,
                    key,
                    visibility,
                    status,
                    effective_date,
                    uploaded_by,
                    hashlib.sha256(text.encode()).hexdigest()[:16] if text else None,
                    len(chunks),
                    note,
                    now_iso(),
                ),
            )
            for i, c in enumerate(chunks):
                t.run(
                    "INSERT INTO doc_chunk (id,document_id,ord,text) VALUES (?,?,?,?)",
                    (new_id(), did, i, c),
                )
        return self.get(did)

    def get(self, did: str) -> dict | None:
        return self.db.one("SELECT * FROM document WHERE id=?", (did,))

    def list(self, *, org_id=None, status=None) -> list[dict]:
        where, p = ["1=1"], []
        if org_id:
            where.append("org_id=?")
            p.append(org_id)
        if status:
            where.append("status=?")
            p.append(status)
        return self.db.q(
            f"SELECT * FROM document WHERE {' AND '.join(where)} ORDER BY created_at DESC", p
        )

    def decide(self, did: str, approve: bool, admin: str) -> None:
        d = self.get(did)
        if not d:
            raise KeyError(did)
        if approve and not d["chunk_count"]:
            raise ValueError(
                "This document has no readable text, so it cannot be used for answers yet."
            )
        self.db.run(
            "UPDATE document SET status=?, decided_by=? WHERE id=?",
            ("approved" if approve else "rejected", admin, did),
        )

    def remove(self, did: str) -> None:
        self.db.run("DELETE FROM doc_chunk WHERE document_id=?", (did,))
        self.db.run("DELETE FROM document WHERE id=?", (did,))
