"""Formats bot-facing text messages in Bahasa Indonesia."""

from __future__ import annotations

from expensebot.models import TIPE_PEMASUKAN, ExpenseRecord
from expensebot.recap import PERIODS, RecapResult, RekapTahunanResult

PERIOD_LABELS = {"harian": "Hari Ini", "mingguan": "Minggu Ini", "bulanan": "Bulan Ini"}
assert set(PERIOD_LABELS) == set(PERIODS)  # noqa: S101 — keeps the two lists from drifting apart

NAMA_BULAN = {
    1: "Januari",
    2: "Februari",
    3: "Maret",
    4: "April",
    5: "Mei",
    6: "Juni",
    7: "Juli",
    8: "Agustus",
    9: "September",
    10: "Oktober",
    11: "November",
    12: "Desember",
}


def format_bulan_label(bulan_yyyymm: str) -> str:
    """"2026-08" -> "Agustus 2026"; falls back to the raw string if it
    doesn't parse (defensive — Rekap Tahunan always writes "yyyy-mm")."""
    try:
        year_str, month_str = bulan_yyyymm.split("-", 1)
        return f"{NAMA_BULAN[int(month_str)]} {int(year_str)}"
    except (ValueError, KeyError):
        return bulan_yyyymm


def _rupiah(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}Rp{abs(value):,.0f}".replace(",", ".")


def format_confirmation(record: ExpenseRecord) -> str:
    tanggal = record.tanggal.strftime("%d %b %Y") if record.tanggal else "❓ tidak terbaca"
    is_pemasukan = record.tipe == TIPE_PEMASUKAN
    lines = [
        "💰 Konfirmasi Pemasukan" if is_pemasukan else "🧾 Konfirmasi Pengeluaran",
        f"Tanggal: {tanggal}",
        f"Merchant: {record.merchant}",
        f"Kategori: {record.kategori}",
        f"Total: {_rupiah(record.total)}",
    ]
    if record.items:
        lines.append("")
        lines.append("Item:")
        for item in record.items:
            lines.append(f"  • {item.nama} — {_rupiah(item.subtotal)}")
    if record.butuh_konfirmasi:
        lines.append("")
        lines.append("⚠️ Beberapa data mungkin perlu dikoreksi manual.")
    return "\n".join(lines)


def format_recap(period: str, result: RecapResult) -> str:
    label = PERIOD_LABELS.get(period, period)
    lines = [
        f"📊 Rekap Keuangan — {label}",
        f"Pemasukan: {_rupiah(result.total_pemasukan)}",
        f"Pengeluaran: {_rupiah(result.total_pengeluaran)}",
        f"Saldo: {_rupiah(result.saldo)}",
        f"({result.jumlah_transaksi} transaksi)",
    ]
    if result.per_kategori_pemasukan:
        lines.append("")
        lines.append("Pemasukan per kategori:")
        for kategori, total in sorted(result.per_kategori_pemasukan.items(), key=lambda kv: -kv[1]):
            lines.append(f"  • {kategori}: {_rupiah(total)}")
    if result.per_kategori:
        lines.append("")
        lines.append("Pengeluaran per kategori:")
        for kategori, total in sorted(result.per_kategori.items(), key=lambda kv: -kv[1]):
            lines.append(f"  • {kategori}: {_rupiah(total)}")
    return "\n".join(lines)


def format_recap_tahunan(result: RekapTahunanResult) -> str:
    lines = [
        f"📆 Rekap Tahunan — {result.tahun}",
        f"Pemasukan: {_rupiah(result.total_pemasukan)}",
        f"Pengeluaran: {_rupiah(result.total_pengeluaran)}",
        f"Saldo: {_rupiah(result.total_saldo)}",
    ]
    if result.per_bulan:
        lines.append("")
        lines.append("Per bulan:")
        for bulan, pemasukan, pengeluaran, saldo in result.per_bulan:
            lines.append(
                f"  • {format_bulan_label(bulan)}: Pemasukan {_rupiah(pemasukan)}, "
                f"Pengeluaran {_rupiah(pengeluaran)}, Saldo {_rupiah(saldo)}"
            )
    else:
        lines.append("")
        lines.append(f"(belum ada data tercatat untuk tahun {result.tahun})")
    return "\n".join(lines)
