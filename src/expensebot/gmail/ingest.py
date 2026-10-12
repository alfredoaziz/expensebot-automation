"""Turns new bank notification emails into sheet rows.

One poll = list matching emails -> skip already-handled IDs -> parse ->
categorize -> dedupe -> append to Google Sheets -> remember the ID. The
returned outcomes are what the Telegram side reports to the user.

Blocking (Gmail/Sheets/Ollama clients are synchronous): call poll() via
asyncio.to_thread.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from expensebot.gmail.message import EmailMessage
from expensebot.gmail.parsers import ParsedTransaction, find_parser
from expensebot.gmail.state import ProcessedStore
from expensebot.llm.client import LLMUnavailableError
from expensebot.models import TIPE_PEMASUKAN, ExpenseItem, ExpenseRecord

logger = logging.getLogger(__name__)


class IngestStatus(StrEnum):
    TERCATAT = "tercatat"
    TRANSFER_SENDIRI = "transfer_sendiri"
    BUKAN_TRANSAKSI = "bukan_transaksi"
    DUPLIKAT = "duplikat"
    GAGAL = "gagal"


@dataclass(frozen=True)
class IngestOutcome:
    status: IngestStatus
    message_id: str
    message: EmailMessage | None = None
    record: ExpenseRecord | None = None
    bank: str = ""
    error: str | None = None


def dedupe_key_for(message_id: str) -> str:
    return f"gmail:{message_id}"


class EmailIngestor:
    def __init__(
        self,
        gmail,
        sheets,
        llm_client,
        store: ProcessedStore,
        query: str,
        account_holder_name: str = "",
        catat_transfer_sendiri: bool = False,
    ) -> None:
        self._gmail = gmail
        self._sheets = sheets
        self._llm = llm_client
        self._store = store
        self._query = query
        self._holder = account_holder_name
        self._catat_transfer_sendiri = catat_transfer_sendiri
        # Failures are retried every poll but reported only once.
        self._reported_failures: set[str] = set()

    def poll(self) -> list[IngestOutcome]:
        new_ids = [
            i for i in self._gmail.list_message_ids(self._query) if i not in self._store
        ]
        outcomes = []
        for message_id in reversed(new_ids):  # Gmail lists newest first
            outcome = self._process(message_id)
            if outcome is not None:
                outcomes.append(outcome)
        return outcomes

    def _process(self, message_id: str) -> IngestOutcome | None:
        message = None
        try:
            message = self._gmail.get_message(message_id)
            outcome = self._ingest(message)
        except Exception as exc:  # noqa: BLE001 — one bad email must not stop the rest
            logger.error("Gagal memproses email %s", message_id, exc_info=True)
            if message_id in self._reported_failures:
                return None
            self._reported_failures.add(message_id)
            return IngestOutcome(
                IngestStatus.GAGAL, message_id, message, error=type(exc).__name__
            )
        self._reported_failures.discard(message_id)
        self._store.add(message_id)
        return outcome

    def _ingest(self, message: EmailMessage) -> IngestOutcome:
        parser = find_parser(message.sender)
        if parser is None:
            record = self._record_via_llm(message)
            bank = message.sender.rsplit("@", 1)[-1]
        else:
            parsed = parser(message, self._holder)
            if parsed is None:
                return IngestOutcome(
                    IngestStatus.BUKAN_TRANSAKSI, message.message_id, message
                )
            bank = parsed.bank
            if parsed.is_self_transfer and not self._catat_transfer_sendiri:
                record = self._record_from_parsed(
                    parsed, message, kategori="Transfer Sendiri"
                )
                return IngestOutcome(
                    IngestStatus.TRANSFER_SENDIRI,
                    message.message_id,
                    message,
                    record,
                    bank,
                )
            record = self._record_from_parsed(parsed, message, self._categorize(parsed))

        if record is None:
            return IngestOutcome(
                IngestStatus.BUKAN_TRANSAKSI, message.message_id, message
            )
        if self._sheets.find_by_dedupe_key(record.dedupe_key):
            return IngestOutcome(
                IngestStatus.DUPLIKAT, message.message_id, message, record, bank
            )
        self._sheets.append_expense(record)
        return IngestOutcome(
            IngestStatus.TERCATAT, message.message_id, message, record, bank
        )

    def _categorize(self, parsed: ParsedTransaction) -> str:
        if parsed.kategori_hint:
            return parsed.kategori_hint
        deskripsi = (
            f"Jenis transaksi: {parsed.jenis}\n"
            f"Merchant/penerima: {parsed.counterparty}\n"
            f"Keterangan: {parsed.keterangan or '-'}"
        )
        try:
            return self._llm.categorize(parsed.tipe, deskripsi)
        except LLMUnavailableError:
            logger.warning(
                "Ollama tidak tersedia — kategori diisi default untuk %s", parsed
            )
            return (
                "Pemasukan Lainnya"
                if parsed.tipe == TIPE_PEMASUKAN
                else "Lain-lain / Tak Terduga"
            )

    @staticmethod
    def _record_from_parsed(
        parsed: ParsedTransaction, message: EmailMessage, kategori: str
    ) -> ExpenseRecord:
        return ExpenseRecord(
            tanggal=parsed.tanggal,
            waktu=parsed.waktu,
            merchant=parsed.counterparty,
            tipe=parsed.tipe,
            kategori=kategori,
            total=parsed.total,
            catatan=parsed.catatan,
            sumber=f"Email {parsed.bank}",
            drive_link=message.gmail_link,
            dedupe_key=dedupe_key_for(message.message_id),
        )

    def _record_via_llm(self, message: EmailMessage) -> ExpenseRecord | None:
        """Fallback for senders without a dedicated parser. Ollama errors
        propagate, so the email is retried on the next poll."""
        result = self._llm.extract_expense_from_text("\n".join(message.lines))
        if not result.valid:
            return None
        return ExpenseRecord(
            tanggal=result.tanggal or message.received_at.date(),
            waktu=message.received_at.strftime("%H:%M"),
            merchant=result.merchant,
            tipe=result.tipe,
            kategori=result.kategori,
            items=[ExpenseItem(**item.model_dump()) for item in result.items],
            total=result.total,
            catatan=result.catatan,
            butuh_konfirmasi=result.butuh_konfirmasi,
            sumber=f"Email {message.sender.rsplit('@', 1)[-1]}",
            drive_link=message.gmail_link,
            dedupe_key=dedupe_key_for(message.message_id),
        )
