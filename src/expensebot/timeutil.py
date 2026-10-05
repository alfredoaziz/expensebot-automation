"""Shared WIB (Asia/Jakarta) time helpers.

Indonesia does not observe DST, so this is a fixed UTC+7 — using it
explicitly matters because container clocks default to UTC.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

WIB = ZoneInfo("Asia/Jakarta")


def now_wib() -> dt.datetime:
    return dt.datetime.now(WIB)


def today_wib() -> dt.date:
    return now_wib().date()
