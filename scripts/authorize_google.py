#!/usr/bin/env python
"""Run this ONCE on the host (not inside Docker) to complete the Google
OAuth desktop-app flow and produce credentials/authorized_user.json.

Requires credentials/client_secret.json (downloaded from Google Cloud
Console: APIs & Services -> Credentials -> OAuth client ID -> Desktop app).
"""

from __future__ import annotations

import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from expensebot.google.auth import SCOPES  # noqa: E402

CREDENTIALS_DIR = Path(__file__).resolve().parent.parent / "credentials"


def main() -> None:
    client_secret_path = CREDENTIALS_DIR / "client_secret.json"
    if not client_secret_path.exists():
        raise SystemExit(
            f"{client_secret_path} tidak ditemukan. Unduh dari Google Cloud "
            "Console (OAuth client ID tipe Desktop app) dan simpan di sana."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
    credentials = flow.run_local_server(port=0)

    token_path = CREDENTIALS_DIR / "authorized_user.json"
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    print(f"Tersimpan: {token_path}")


if __name__ == "__main__":
    main()
