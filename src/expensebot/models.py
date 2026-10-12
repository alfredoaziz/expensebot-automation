"""Domain models shared across the bot's modules."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

TIPE_PENGELUARAN = "Pengeluaran"
TIPE_PEMASUKAN = "Pemasukan"

# Berdasarkan kategorisasi_pengeluaran_bulanan.md, ditambah "E-commerce"
# untuk topup/transfer ke platform seperti Shopee/ShopeePay/GoPay/Tokopedia.
KATEGORI_PENGELUARAN = [
    "Kebutuhan Pokok",
    "Makan & Minum",
    "Transportasi",
    "Kesehatan & Kebersihan Diri",
    "Rumah Tangga",
    "Anak & Keluarga",
    "Pendidikan & Pengembangan Diri",
    "Hiburan & Gaya Hidup",
    "Sosial & Kewajiban",
    "Pakaian & Penampilan",
    "Tabungan & Investasi",
    "Pajak & Administrasi",
    "E-commerce",
    "Lain-lain / Tak Terduga",
]

# Kategori pemasukan — terinspirasi dari BUDGET-TRACKER.xlsx ("Kerja
# (pemasukan)", "Return (by investment)", dll), disederhanakan.
KATEGORI_PEMASUKAN = [
    "Gaji",
    "Freelance/Bonus",
    "Hasil Investasi",
    "Transfer Masuk",
    "Pemasukan Lainnya",
]


class ExpenseItem(BaseModel):
    nama: str
    qty: float = 1
    harga_satuan: float = 0
    subtotal: float


class ExpenseRecord(BaseModel):
    tanggal: date | None
    merchant: str
    kategori: str
    items: list[ExpenseItem] = Field(default_factory=list)
    total: float
    mata_uang: str = "IDR"
    catatan: str | None = None
    butuh_konfirmasi: bool = False
    # Default "Pengeluaran" so rows saved before this field existed keep
    # working: recap.py treats a blank/missing Tipe the same way.
    tipe: Literal["Pengeluaran", "Pemasukan"] = TIPE_PENGELUARAN

    # "HH:MM" WIB of the transaction itself when known (bank emails carry
    # it); None falls back to the time the row is saved.
    waktu: str | None = None

    sumber: str = "Foto"
    drive_link: str | None = None
    dedupe_key: str | None = None
