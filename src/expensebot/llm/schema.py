"""Pydantic schema used to validate the raw JSON returned by the vision LLM."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from expensebot.models import (
    KATEGORI_PEMASUKAN,
    KATEGORI_PENGELUARAN,
    TIPE_PEMASUKAN,
    TIPE_PENGELUARAN,
)


class LlmExpenseItem(BaseModel):
    nama: str
    qty: float = 1
    harga_satuan: float = 0
    subtotal: float = 0


class LlmExtractionResult(BaseModel):
    """Mirrors the JSON schema described in llm/prompts.py's SYSTEM_PROMPT."""

    valid: bool
    alasan: str | None = None
    tanggal: date | None = None
    merchant: str = ""
    tipe: Literal["Pengeluaran", "Pemasukan"] = TIPE_PENGELUARAN
    kategori: str = "Lain-lain / Tak Terduga"
    items: list[LlmExpenseItem] = Field(default_factory=list)
    total: float = 0
    mata_uang: str = "IDR"
    catatan: str | None = None
    butuh_konfirmasi: bool = False

    @model_validator(mode="after")
    def _kategori_sesuai_tipe(self) -> LlmExtractionResult:
        """kategori must come from the list matching tipe — an expense
        category leaking onto an income row (or vice versa) would silently
        break the Dashboard's Pengeluaran/Pemasukan filters."""
        if self.tipe == TIPE_PEMASUKAN:
            if self.kategori not in KATEGORI_PEMASUKAN:
                self.kategori = "Pemasukan Lainnya"
        elif self.kategori not in KATEGORI_PENGELUARAN:
            self.kategori = "Lain-lain / Tak Terduga"
        return self
