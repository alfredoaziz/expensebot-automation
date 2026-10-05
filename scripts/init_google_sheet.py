#!/usr/bin/env python
"""Manually (re)ensure the current month's Transaksi tab, Rekap Tahunan,
and Dashboard exist (and migrate a legacy flat "Transaksi" tab into
per-month tabs if one is found). main.py already calls this on every
startup, so this script is only needed for debugging or re-running the
setup without starting the whole bot."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from expensebot.config import load_settings  # noqa: E402
from expensebot.google.auth import load_credentials  # noqa: E402
from expensebot.google.sheets import SheetsRepository  # noqa: E402


def main() -> None:
    settings = load_settings()
    credentials = load_credentials(settings.google_credentials_dir)
    sheets_repo = SheetsRepository(credentials, settings.google_spreadsheet_id)
    sheets_repo.ensure_structure()
    print("Struktur Google Sheet siap (tab Transaksi bulanan + Rekap Tahunan + Dashboard).")


if __name__ == "__main__":
    main()
