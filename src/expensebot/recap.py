"""Pure aggregation logic over rows read from the Transaksi sheet.

Kept free of any Google/Telegram dependency so it is trivial to unit test.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

from expensebot.models import TIPE_PEMASUKAN, TIPE_PENGELUARAN

PERIODS = ("harian", "mingguan", "bulanan")


@dataclass(frozen=True)
class RecapResult:
    total_pengeluaran: float
    total_pemasukan: float = 0.0
    per_kategori: dict[str, float] = field(default_factory=dict)
    per_kategori_pemasukan: dict[str, float] = field(default_factory=dict)
    jumlah_transaksi: int = 0

    @property
    def saldo(self) -> float:
        return self.total_pemasukan - self.total_pengeluaran


def _parse_tanggal(value: str) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _period_start(today: dt.date, period: str) -> dt.date:
    if period == "harian":
        return today
    if period == "mingguan":
        return today - dt.timedelta(days=today.weekday())
    if period == "bulanan":
        return today.replace(day=1)
    raise ValueError(f"Periode tidak dikenal: {period}")


def summarize(
    rows: list[dict], period: str, today: dt.date | None = None
) -> RecapResult:
    today = today or dt.date.today()
    start = _period_start(today, period)

    total_pengeluaran = 0.0
    total_pemasukan = 0.0
    per_kategori: dict[str, float] = defaultdict(float)
    per_kategori_pemasukan: dict[str, float] = defaultdict(float)
    count = 0

    for row in rows:
        tanggal = _parse_tanggal(str(row.get("Tanggal", "")))
        if tanggal is None or not (start <= tanggal <= today):
            continue
        nominal = float(row.get("Total (Rp)", 0) or 0)
        # Rows saved before the Tipe column existed have it blank —
        # treat those as Pengeluaran, matching what they always were.
        tipe = str(row.get("Tipe") or TIPE_PENGELUARAN)
        kategori = str(row.get("Kategori", "Lain-lain / Tak Terduga"))
        if tipe == TIPE_PEMASUKAN:
            total_pemasukan += nominal
            per_kategori_pemasukan[kategori] += nominal
        else:
            total_pengeluaran += nominal
            per_kategori[kategori] += nominal
        count += 1

    return RecapResult(
        total_pengeluaran=total_pengeluaran,
        total_pemasukan=total_pemasukan,
        per_kategori=dict(per_kategori),
        per_kategori_pemasukan=dict(per_kategori_pemasukan),
        jumlah_transaksi=count,
    )


@dataclass(frozen=True)
class RekapTahunanResult:
    tahun: int
    # (bulan "yyyy-mm", pemasukan, pengeluaran, saldo), one per tracked month.
    per_bulan: list[tuple[str, float, float, float]] = field(default_factory=list)
    total_pemasukan: float = 0.0
    total_pengeluaran: float = 0.0

    @property
    def total_saldo(self) -> float:
        return self.total_pemasukan - self.total_pengeluaran


def _to_float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def summarize_tahunan(rekap_tahunan_rows: list[dict], tahun: int) -> RekapTahunanResult:
    """Filters rows already aggregated per-month by the "Rekap Tahunan"
    Google Sheet tab (columns Bulan/Pemasukan/Pengeluaran/Saldo) down to
    one calendar year. Its own "Total" row never matches the "yyyy-" prefix
    check, so it's excluded without special-casing."""
    prefix = f"{tahun:04d}-"
    per_bulan: list[tuple[str, float, float, float]] = []
    total_pemasukan = 0.0
    total_pengeluaran = 0.0

    for row in rekap_tahunan_rows:
        bulan = str(row.get("Bulan", ""))
        if not bulan.startswith(prefix):
            continue
        pemasukan = _to_float(row.get("Pemasukan"))
        pengeluaran = _to_float(row.get("Pengeluaran"))
        saldo = _to_float(row.get("Saldo"))
        per_bulan.append((bulan, pemasukan, pengeluaran, saldo))
        total_pemasukan += pemasukan
        total_pengeluaran += pengeluaran

    per_bulan.sort(key=lambda item: item[0])
    return RekapTahunanResult(
        tahun=tahun,
        per_bulan=per_bulan,
        total_pemasukan=total_pemasukan,
        total_pengeluaran=total_pengeluaran,
    )
