"""Read-only access to the user's Gmail inbox (scope gmail.readonly)."""

from __future__ import annotations

import base64
import datetime as dt
from email.utils import parseaddr

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from expensebot.gmail.html_text import html_to_lines, plain_text_to_lines
from expensebot.gmail.message import EmailMessage
from expensebot.timeutil import WIB

_MAX_RESULTS = 50


def _decode_body(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _collect_bodies(payload: dict, bodies: dict[str, str]) -> None:
    """Depth-first walk of the MIME tree, keeping the first text/html and
    text/plain part found."""
    mime_type = payload.get("mimeType", "")
    data = payload.get("body", {}).get("data")
    if data and mime_type in ("text/html", "text/plain") and mime_type not in bodies:
        bodies[mime_type] = _decode_body(data)
    for part in payload.get("parts", []) or []:
        _collect_bodies(part, bodies)


def message_from_api(raw: dict) -> EmailMessage:
    """Builds an EmailMessage from a users.messages.get(format="full")
    response."""
    payload = raw.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    bodies: dict[str, str] = {}
    _collect_bodies(payload, bodies)
    if "text/html" in bodies:
        lines = html_to_lines(bodies["text/html"])
    else:
        lines = plain_text_to_lines(bodies.get("text/plain", ""))

    received_at = dt.datetime.fromtimestamp(
        int(raw.get("internalDate", "0")) / 1000, tz=WIB
    )
    return EmailMessage(
        message_id=raw["id"],
        sender=parseaddr(headers.get("from", ""))[1].lower(),
        subject=headers.get("subject", ""),
        received_at=received_at,
        lines=tuple(lines),
    )


class GmailRepository:
    def __init__(self, credentials: Credentials) -> None:
        self._service = build(
            "gmail", "v1", credentials=credentials, cache_discovery=False
        )

    def list_message_ids(self, query: str) -> list[str]:
        """Newest-first IDs of messages matching a Gmail search query."""
        response = (
            self._service.users()
            .messages()
            .list(userId="me", q=query, maxResults=_MAX_RESULTS)
            .execute()
        )
        return [m["id"] for m in response.get("messages", [])]

    def get_message(self, message_id: str) -> EmailMessage:
        raw = (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        return message_from_api(raw)
