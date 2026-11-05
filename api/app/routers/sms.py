"""POST /sms/inbound: text-based access to search, for people without reliable smartphone
data or who'd rather text than use an app.

Reuses the same plain-language search as the web UI (search/query/intent.py +
search/query/search.py), so results are the same "never a flagged or inactive listing"
guarantee. Built against Twilio's inbound-message webhook shape (form-encoded From/Body,
TwiML reply) since that's the most common SMS gateway to prototype against; a different
provider (e.g. Amazon Pinpoint/SNS) would need its own thin adapter in front of
``reply_text``, which carries none of the Twilio-specific shape.

Protected by a shared secret (RE_SMS_WEBHOOK_TOKEN), not the dashboard's bearer token --
Twilio can't send one, only whatever this endpoint's own webhook URL embeds.
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Annotated
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, Form, HTTPException, Query, status
from fastapi.responses import Response

from api.app.deps import get_store
from api.app.store_pg import PgHsdsStore
from search.query.intent import plan
from search.query.search import get_backend

log = logging.getLogger(__name__)

router = APIRouter(tags=["SMS"])

MAX_RESULTS = 3
SMS_CHAR_LIMIT = 1500  # a few SMS segments' worth; keeps the reply from being cut off
NO_QUERY = (
    'Text us what you need help with, like "food" or "help with rent", '
    "and we'll text back resources."
)


def _require_webhook_token(token: str | None) -> None:
    expected = os.environ.get("RE_SMS_WEBHOOK_TOKEN")
    if not expected or not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid webhook token")


def reply_text(store: PgHsdsStore, body: str) -> str:
    """The plain-text reply for one inbound message: up to MAX_RESULTS resources, or a
    clear "nothing found" / "say more" message. Never a flagged or inactive listing --
    get_backend().search() already filters those out, same as the web search."""
    text = (body or "").strip()
    if len(text) < 2:
        return NO_QUERY
    results = get_backend(store).search(plan(text), limit=MAX_RESULTS, offset=0)
    if not results.hits:
        return f'No matches for "{text}". Try different or fewer words, like "food" or "shelter".'
    lines = [
        f"{i + 1}. {hit.name}" + (f" - {hit.phone}" if hit.phone else "")
        for i, hit in enumerate(results.hits)
    ]
    return ("Resources:\n" + "\n".join(lines))[:SMS_CHAR_LIMIT]


@router.post("/sms/inbound")
def sms_inbound(
    store: Annotated[PgHsdsStore, Depends(get_store)],
    From: Annotated[str, Form()] = "",  # noqa: N803 - Twilio's field name
    Body: Annotated[str, Form()] = "",  # noqa: N803 - Twilio's field name
    token: Annotated[str | None, Query()] = None,
) -> Response:
    _require_webhook_token(token)
    reply = reply_text(store, Body)
    log.info(
        "sms inbound",
        extra={"from_suffix": From[-4:] if From else None, "reply_chars": len(reply)},
    )
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Message>{escape(reply)}</Message></Response>"
    )
    return Response(content=twiml, media_type="application/xml")
