"""Plain data carrier for one fetched Gmail message."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class EmailMessage:
    message_id: str
    sender: str  # bare, lowercased address, e.g. "receipts@blubybcadigital.id"
    subject: str
    received_at: dt.datetime  # WIB
    lines: tuple[str, ...]  # body text, one line per HTML block/cell

    @property
    def gmail_link(self) -> str:
        return f"https://mail.google.com/mail/u/0/#all/{self.message_id}"
