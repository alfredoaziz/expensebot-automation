"""Loads the OAuth credentials produced once by scripts/authorize_google.py
and keeps them refreshed for the lifetime of the process."""

from __future__ import annotations

import logging
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)

# Read-only on purpose: the bot only needs to read bank notification
# emails — it never sends, labels, or deletes anything in Gmail.
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    GMAIL_SCOPE,
]


class GoogleAuthNotConfiguredError(Exception):
    """Raised when authorized_user.json is missing (run scripts/authorize_google.py)."""


def load_credentials(credentials_dir: str) -> Credentials:
    token_path = Path(credentials_dir) / "authorized_user.json"
    if not token_path.exists():
        raise GoogleAuthNotConfiguredError(
            f"{token_path} tidak ditemukan. Jalankan scripts/authorize_google.py "
            "sekali di host untuk membuatnya."
        )

    # No scopes override: refresh with exactly what was granted. Forcing
    # SCOPES here would make Google reject the refresh (invalid_scope) for a
    # token authorized before the Gmail scope existed — the bot must keep
    # working on Sheets/Drive until the user re-runs the authorize script.
    creds = Credentials.from_authorized_user_file(str(token_path))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _try_persist_refreshed_token(token_path, creds)
    return creds


def has_scope(creds: Credentials, scope: str) -> bool:
    return scope in (creds.scopes or [])


def _try_persist_refreshed_token(token_path: Path, creds: Credentials) -> None:
    """Best-effort: saves the refreshed access token so the next startup
    can skip a refresh round-trip. Not required for correctness — the
    refresh_token itself is still valid and creds.refresh() above already
    succeeded — so a failure here (e.g. credentials/ mounted read-only in
    Docker) must never crash the bot."""
    try:
        token_path.write_text(creds.to_json(), encoding="utf-8")
    except OSError:
        logger.warning(
            "Tidak bisa menyimpan token yang di-refresh ke %s (kemungkinan read-only). "
            "Tidak masalah — token tetap dipakai untuk sesi ini.",
            token_path,
        )
