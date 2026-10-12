#!/usr/bin/env python
"""Shows how the bot WOULD read your latest bank notification emails —
nothing is written to Google Sheets and nothing is sent to Telegram.

Run it inside the bot container (env + credentials already there):

    docker compose exec expensebot python scripts/gmail_dry_run.py --limit 10

Optional: --query "from:bankbsi.co.id newer_than:7d" to try another search.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from expensebot.config import Settings  # noqa: E402
from expensebot.gmail.client import GmailRepository  # noqa: E402
from expensebot.gmail.parsers import find_parser  # noqa: E402
from expensebot.google.auth import (
    GMAIL_SCOPE,
    has_scope,
    load_credentials,
)  # noqa: E402

_LOCAL_CREDENTIALS_DIR = Path(__file__).resolve().parent.parent / "credentials"


def main() -> None:
    default_query = Settings.model_fields["gmail_query"].default.replace("1d", "7d")
    arg_parser = argparse.ArgumentParser(description=__doc__)
    arg_parser.add_argument("--query", default=default_query)
    arg_parser.add_argument("--limit", type=int, default=10)
    args = arg_parser.parse_args()

    credentials_dir = os.environ.get("GOOGLE_CREDENTIALS_DIR", "")
    if not credentials_dir or not Path(credentials_dir).exists():
        credentials_dir = str(_LOCAL_CREDENTIALS_DIR)
    credentials = load_credentials(credentials_dir)
    if not has_scope(credentials, GMAIL_SCOPE):
        raise SystemExit(
            "Token belum punya izin Gmail. Jalankan ulang scripts/authorize_google.py di host."
        )

    holder = os.environ.get("ACCOUNT_HOLDER_NAME", "")
    gmail = GmailRepository(credentials)
    ids = gmail.list_message_ids(args.query)[: args.limit]
    print(f"Query: {args.query} -> {len(ids)} email (maks {args.limit})\n")

    for message_id in ids:
        msg = gmail.get_message(message_id)
        parser = find_parser(msg.sender)
        print(f"[{msg.received_at:%Y-%m-%d %H:%M}] {msg.sender} | {msg.subject}")
        if parser is None:
            print("   -> belum ada parser; bot akan memakai LLM (fallback)\n")
            continue
        parsed = parser(msg, holder)
        if parsed is None:
            print("   -> bukan transaksi (diabaikan)\n")
            continue
        sendiri = "  [TRANSFER SENDIRI]" if parsed.is_self_transfer else ""
        print(
            f"   -> {parsed.tipe} Rp{parsed.total:,.2f} | {parsed.counterparty} | "
            f"{parsed.tanggal} {parsed.waktu} | kategori: {parsed.kategori_hint or '(LLM)'}"
            f"{sendiri}\n      catatan: {parsed.catatan}\n"
        )


if __name__ == "__main__":
    main()
