"""Deterministic parsers for mobile-banking notification emails.

Bank emails are fixed templates, so plain label lookups are both faster
and far more reliable than asking the small local LLM to read them —
especially for amounts, where the LLM was already prone to dropping digits
on receipts. Only the *category* of an outgoing payment is left to the LLM
(see gmail/ingest.py); senders without a parser here fall back to the
LLM's full text extraction.

Each parser returns None when the email is not a transaction (promo,
e-statement notice, ...), so it can be skipped silently.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from expensebot.gmail.message import EmailMessage
from expensebot.models import TIPE_PEMASUKAN, TIPE_PENGELUARAN

KATEGORI_E_COMMERCE = "E-commerce"

_RUPIAH_RE = re.compile(r"(?:Rp|IDR)\.?\s*(\d[\d.]*(?:,\d{1,2})?)", re.IGNORECASE)
_DATE_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})")
_TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})(?::\d{2})?\b")
_E_COMMERCE_RE = re.compile(
    r"shopee|gopay|go-pay|tokopedia|\bovo\b|lazada|blibli", re.IGNORECASE
)
_INCOMING_RE = re.compile(r"masuk|diterima|terima transfer|kredit", re.IGNORECASE)
_GAJI_RE = re.compile(r"gaji|payroll|salary", re.IGNORECASE)
_ACCOUNT_DIGITS_RE = re.compile(r"[\d\s•*\-]+")

_BULAN = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "mei": 5,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "agu": 8,
    "agt": 8,
    "aug": 8,
    "sep": 9,
    "okt": 10,
    "oct": 10,
    "nov": 11,
    "des": 12,
    "dec": 12,
}


@dataclass(frozen=True)
class ParsedTransaction:
    bank: str
    tipe: str
    jenis: str  # e.g. "QRIS", "Transfer", "Top Up E-Wallet", "BI-FAST"
    tanggal: dt.date
    waktu: str | None  # "HH:MM" WIB
    counterparty: str  # merchant, recipient, or sender
    total: float
    catatan: str | None
    keterangan: str | None
    kategori_hint: str | None
    is_self_transfer: bool


Parser = Callable[[EmailMessage, str], ParsedTransaction | None]


# --- shared helpers ----------------------------------------------------------


def parse_rupiah(text: str | None) -> float | None:
    """Indonesian format: "." groups thousands, "," starts the cents —
    "Rp 24.000.000" -> 24000000, "Rp183.110,71" -> 183110.71."""
    if not text:
        return None
    match = _RUPIAH_RE.search(text)
    if not match:
        return None
    number = match.group(1).replace(".", "").replace(",", ".")
    return round(float(number), 2)


def parse_tanggal_waktu(text: str | None) -> tuple[dt.date | None, str | None]:
    if not text:
        return None, None
    tanggal = None
    date_match = _DATE_RE.search(text)
    if date_match:
        month = _BULAN.get(date_match.group(2)[:3].lower())
        if month:
            try:
                tanggal = dt.date(
                    int(date_match.group(3)), month, int(date_match.group(1))
                )
            except ValueError:
                tanggal = None
    time_match = _TIME_RE.search(text)
    waktu = (
        f"{int(time_match.group(1)):02d}:{time_match.group(2)}" if time_match else None
    )
    return tanggal, waktu


def _value_after(lines: Sequence[str], *labels: str) -> str | None:
    """Value cell that follows the first line exactly matching one of
    `labels` (tried in priority order)."""
    for label in labels:
        wanted = label.casefold()
        for index, line in enumerate(lines[:-1]):
            if line.casefold() == wanted:
                return lines[index + 1]
    return None


def _normalize_name(name: str) -> str:
    return re.sub(r"[^A-Z]", "", name.upper())


def is_account_holder(name: str | None, account_holder_name: str) -> bool:
    """Banks print the same name differently ("BUDISANTOSO" vs
    "Budi Santoso") and sometimes truncate it, so compare letters only
    and accept a truncated prefix."""
    holder = _normalize_name(account_holder_name)
    candidate = _normalize_name(name or "")
    if not holder or not candidate:
        return False
    return candidate == holder or (len(candidate) >= 8 and holder.startswith(candidate))


def _rp(value: float) -> str:
    return f"Rp{value:,.0f}".replace(",", ".")


def _join_catatan(parts: list[str | None]) -> str | None:
    kept = [part for part in parts if part]
    return " · ".join(kept) or None


def _kategori_hint(
    tipe: str, jenis: str, counterparty: str, keterangan: str | None
) -> str | None:
    if tipe == TIPE_PEMASUKAN:
        text = f"{jenis} {keterangan or ''}"
        return "Gaji" if _GAJI_RE.search(text) else "Transfer Masuk"
    if "top up" in jenis.lower() or _E_COMMERCE_RE.search(counterparty):
        return KATEGORI_E_COMMERCE
    return None  # let the LLM pick from the merchant/recipient name


def _fallback_tanggal_waktu(
    msg: EmailMessage, tanggal: dt.date | None, waktu: str | None
) -> tuple[dt.date, str]:
    return tanggal or msg.received_at.date(), waktu or msg.received_at.strftime("%H:%M")


# --- BSI (BYOND) ---------------------------------------------------------------

_BSI_NOMINAL_LABELS = (
    "Nominal Transaksi",
    "Nominal Top Up",
    "Nominal Pembayaran",
    "Nominal Transfer",
    "Nominal",
)


def _bsi_counterparty(lines: Sequence[str], is_incoming: bool) -> tuple[str, str]:
    """Returns (jenis, counterparty) from whichever template-specific
    field is present."""
    merchant = _value_after(lines, "Nama Merchant")
    if merchant:
        return "QRIS", merchant
    ewallet = _value_after(lines, "Pembelian")
    if ewallet:
        return "Top Up E-Wallet", f"Top Up {ewallet}"
    biller = _value_after(lines, "Pembayaran")
    if biller:
        return "Pembayaran", biller
    if is_incoming:
        sender = _value_after(lines, "Rekening Pengirim", "Nama Pengirim", "Pengirim")
        return "Transfer Masuk", sender or "Transfer Masuk"
    recipient = _value_after(lines, "Rekening Penerima", "Nama Penerima", "Penerima")
    return "Transfer", recipient or "Transfer"


def parse_bsi(msg: EmailMessage, account_holder_name: str) -> ParsedTransaction | None:
    lines = msg.lines
    nominal = parse_rupiah(_value_after(lines, *_BSI_NOMINAL_LABELS))
    if nominal is None:
        return None

    heading = f"{msg.subject} {' '.join(lines[:3])}"
    is_incoming = bool(_INCOMING_RE.search(heading))
    tipe = TIPE_PEMASUKAN if is_incoming else TIPE_PENGELUARAN
    jenis, counterparty = _bsi_counterparty(lines, is_incoming)

    biaya = parse_rupiah(_value_after(lines, "Biaya Transfer", "Biaya Admin")) or 0
    total = parse_rupiah(_value_after(lines, "Total"))
    if total is None:
        total = nominal + (0 if is_incoming else biaya)

    tanggal, _ = parse_tanggal_waktu(_value_after(lines, "Tanggal"))
    _, waktu = parse_tanggal_waktu(_value_after(lines, "Jam"))
    tanggal, waktu = _fallback_tanggal_waktu(msg, tanggal, waktu)
    keterangan = _value_after(lines, "Keterangan", "Berita")

    return ParsedTransaction(
        bank="BSI",
        tipe=tipe,
        jenis=jenis,
        tanggal=tanggal,
        waktu=waktu,
        counterparty=counterparty,
        total=total,
        catatan=_join_catatan(
            [
                jenis,
                keterangan,
                f"Biaya {_rp(biaya)}" if biaya else None,
                "via email BSI",
            ]
        ),
        keterangan=keterangan,
        kategori_hint=_kategori_hint(tipe, jenis, counterparty, keterangan),
        is_self_transfer=jenis.startswith("Transfer")
        and is_account_holder(counterparty, account_holder_name),
    )


# --- blu by BCA Digital --------------------------------------------------------

_BLU_AMOUNT_LABELS = (
    "Total Bayar",
    "Nominal Transfer",
    "Nominal Transaksi",
    "Total Pembayaran",
)
_BLU_BLOCK_STOP = (
    "tgl & jam",
    "tipe transaksi",
    "nominal dalam",
    "catatan",
    "berita",
    "no. ref",
)


def _index_of(lines: Sequence[str], labels: Sequence[str]) -> int | None:
    wanted = {label.casefold() for label in labels}
    for index, line in enumerate(lines):
        if line.casefold() in wanted:
            return index
    return None


def _blu_party_block(lines: Sequence[str], start: int) -> list[str]:
    """The "from → to" card under the amount: [from name, from bank/account,
    to name, to bank/account, ...] until the next labelled field."""
    block: list[str] = []
    for line in lines[start:]:
        if line.casefold().startswith(_BLU_BLOCK_STOP):
            break
        block.append(line)
    return block


def _blu_outgoing_counterparty(block: list[str], account_holder_name: str) -> str:
    # Left side is always the user's own "<name> / bluAccount"; the
    # merchant/recipient is the first real name after that.
    after_own_account = False
    for line in block:
        if line.casefold().startswith("bluaccount"):
            after_own_account = True
            continue
        if after_own_account and not _ACCOUNT_DIGITS_RE.fullmatch(line):
            return line
    for line in block:
        if not line.casefold().startswith("blu") and not is_account_holder(
            line, account_holder_name
        ):
            return line
    return "Transaksi blu"


def parse_blu(msg: EmailMessage, account_holder_name: str) -> ParsedTransaction | None:
    lines = msg.lines
    amount_index = _index_of(lines, _BLU_AMOUNT_LABELS)
    if amount_index is None or amount_index + 1 >= len(lines):
        return None
    total = parse_rupiah(lines[amount_index + 1])
    if total is None:
        return None

    block = _blu_party_block(lines, amount_index + 2)
    intro = f"{msg.subject} {' '.join(lines[:amount_index])}"
    is_incoming = bool(_INCOMING_RE.search(intro))
    tipe = TIPE_PEMASUKAN if is_incoming else TIPE_PENGELUARAN

    extras: list[str | None] = []
    if is_incoming:
        counterparty = block[0] if block else "Transfer Masuk"
        extras.append(f"dari {block[1]}" if len(block) > 1 else None)
    else:
        counterparty = _blu_outgoing_counterparty(block, account_holder_name)

    foreign_index = next(
        (
            i
            for i, line in enumerate(lines[:-1])
            if line.casefold().startswith("nominal dalam")
        ),
        None,
    )
    if foreign_index is not None:
        extras.append(lines[foreign_index + 1])

    tanggal, waktu = parse_tanggal_waktu(_value_after(lines, "Tgl & Jam Transaksi"))
    tanggal, waktu = _fallback_tanggal_waktu(msg, tanggal, waktu)
    jenis = _value_after(lines, "Tipe Transaksi") or (
        "Transfer Masuk" if is_incoming else "Transaksi blu"
    )
    keterangan = _value_after(lines, "Catatan", "Berita", "Keterangan")

    return ParsedTransaction(
        bank="blu",
        tipe=tipe,
        jenis=jenis,
        tanggal=tanggal,
        waktu=waktu,
        counterparty=counterparty,
        total=total,
        catatan=_join_catatan([jenis, keterangan, *extras, "via email blu"]),
        keterangan=keterangan,
        kategori_hint=_kategori_hint(tipe, jenis, counterparty, keterangan),
        is_self_transfer=is_account_holder(counterparty, account_holder_name),
    )


# --- dispatch ----------------------------------------------------------------

_PARSERS_BY_DOMAIN: dict[str, Parser] = {
    "bankbsi.co.id": parse_bsi,
    "blubybcadigital.id": parse_blu,
}


def find_parser(sender: str) -> Parser | None:
    domain = sender.rsplit("@", 1)[-1].lower()
    for known_domain, parser in _PARSERS_BY_DOMAIN.items():
        if domain == known_domain or domain.endswith(f".{known_domain}"):
            return parser
    return None
