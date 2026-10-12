"""EmailIngestor tests with in-memory fakes (synthetic data only)."""

import datetime as dt

import pytest

from expensebot.gmail.html_text import html_to_lines
from expensebot.gmail.ingest import EmailIngestor, IngestStatus, dedupe_key_for
from expensebot.gmail.message import EmailMessage
from expensebot.gmail.state import ProcessedStore
from expensebot.llm.client import LLMUnavailableError
from expensebot.llm.schema import LlmExtractionResult
from expensebot.telegram.formatting import format_email_report
from expensebot.timeutil import WIB

BSI = "nonereply.byondbybsi@bankbsi.co.id"


def _message(message_id: str, sender: str, subject: str, html: str) -> EmailMessage:
    return EmailMessage(
        message_id=message_id,
        sender=sender,
        subject=subject,
        received_at=dt.datetime(2026, 10, 9, 12, 31, tzinfo=WIB),
        lines=tuple(html_to_lines(html)),
    )


def _bsi_qris(
    message_id: str, merchant: str = "WARUNG MAKAN SEDERHANA"
) -> EmailMessage:
    html = (
        "<p>Transaksi pembayaran QRIS MPM kamu berhasil</p><table>"
        "<tr><td>Nominal Transaksi</td><td>Rp 45.000,00</td></tr>"
        "<tr><td>Tanggal</td><td>09 Okt 2026</td></tr>"
        "<tr><td>Jam</td><td>12:30:00 WIB</td></tr>"
        f"<tr><td>Nama Merchant</td><td>{merchant}</td></tr>"
        "<tr><td>Total</td><td>Rp 45.000,00</td></tr></table>"
    )
    return _message(
        message_id, BSI, "Transaksi Pembayaran QRIS MPM Kamu Berhasil", html
    )


def _bsi_transfer_ke_sendiri(message_id: str) -> EmailMessage:
    html = (
        "<p>Transfer Berhasil</p><p>Rekening Penerima</p><p>BUDI SANTOSO</p>"
        "<table><tr><td>Nominal Transfer</td><td>Rp 600.000</td></tr></table>"
    )
    return _message(message_id, BSI, "Notifikasi Transaksi Transfer", html)


class _FakeGmail:
    def __init__(self, messages: list[EmailMessage]):
        self._messages = {m.message_id: m for m in messages}
        self._order = [m.message_id for m in reversed(messages)]  # newest first
        self.fail_ids: set[str] = set()

    def list_message_ids(self, _query: str) -> list[str]:
        return list(self._order)

    def get_message(self, message_id: str) -> EmailMessage:
        if message_id in self.fail_ids:
            raise ConnectionError("gmail down")
        return self._messages[message_id]


class _FakeSheets:
    def __init__(self, existing_keys: set[str] | None = None):
        self.existing_keys = set(existing_keys or ())
        self.appended = []

    def find_by_dedupe_key(self, key: str) -> bool:
        return key in self.existing_keys

    def append_expense(self, record) -> None:
        self.appended.append(record)
        self.existing_keys.add(record.dedupe_key)


class _FakeLlm:
    def __init__(self, kategori: str = "Makan & Minum", down: bool = False):
        self._kategori = kategori
        self._down = down
        self.categorize_calls = 0
        self.extract_result = LlmExtractionResult(valid=False, alasan="promo")

    def categorize(self, _tipe: str, _deskripsi: str) -> str:
        self.categorize_calls += 1
        if self._down:
            raise LLMUnavailableError("down")
        return self._kategori

    def extract_expense_from_text(self, _text: str) -> LlmExtractionResult:
        if self._down:
            raise LLMUnavailableError("down")
        return self.extract_result


@pytest.fixture
def state_path(tmp_path):
    return str(tmp_path / "state.json")


def _ingestor(gmail, sheets, llm, state_path, **kwargs) -> EmailIngestor:
    return EmailIngestor(
        gmail,
        sheets,
        llm,
        ProcessedStore(state_path),
        query="q",
        account_holder_name="Budi Santoso",
        **kwargs,
    )


def test_qris_baru_tercatat_dengan_kategori_llm_dan_waktu_transaksi(state_path):
    # Arrange
    gmail, sheets, llm = _FakeGmail([_bsi_qris("m1")]), _FakeSheets(), _FakeLlm()

    # Act
    outcomes = _ingestor(gmail, sheets, llm, state_path).poll()

    # Assert
    assert [o.status for o in outcomes] == [IngestStatus.TERCATAT]
    record = sheets.appended[0]
    assert record.total == 45000
    assert record.kategori == "Makan & Minum"
    assert record.waktu == "12:30"
    assert record.sumber == "Email BSI"
    assert record.dedupe_key == dedupe_key_for("m1")


def test_email_yang_sudah_diproses_tidak_dilaporkan_lagi_termasuk_setelah_restart(
    state_path,
):
    # Arrange
    gmail, sheets, llm = _FakeGmail([_bsi_qris("m1")]), _FakeSheets(), _FakeLlm()
    ingestor = _ingestor(gmail, sheets, llm, state_path)
    ingestor.poll()

    # Act
    second = ingestor.poll()
    after_restart = _ingestor(gmail, sheets, llm, state_path).poll()

    # Assert
    assert second == []
    assert after_restart == []
    assert len(sheets.appended) == 1


def test_transfer_ke_rekening_sendiri_dilaporkan_tapi_tidak_dicatat(state_path):
    # Arrange
    gmail = _FakeGmail([_bsi_transfer_ke_sendiri("m2")])
    sheets, llm = _FakeSheets(), _FakeLlm()

    # Act
    outcomes = _ingestor(gmail, sheets, llm, state_path).poll()

    # Assert
    assert outcomes[0].status == IngestStatus.TRANSFER_SENDIRI
    assert sheets.appended == []
    assert llm.categorize_calls == 0
    assert "Transfer antar rekening sendiri" in format_email_report(outcomes[0])


def test_transfer_sendiri_tetap_dicatat_kalau_diaktifkan(state_path):
    gmail = _FakeGmail([_bsi_transfer_ke_sendiri("m2")])
    sheets = _FakeSheets()

    outcomes = _ingestor(
        gmail, sheets, _FakeLlm(), state_path, catat_transfer_sendiri=True
    ).poll()

    assert outcomes[0].status == IngestStatus.TERCATAT
    assert len(sheets.appended) == 1


def test_sudah_ada_di_sheet_jadi_duplikat_tanpa_laporan(state_path):
    gmail = _FakeGmail([_bsi_qris("m1")])
    sheets = _FakeSheets(existing_keys={dedupe_key_for("m1")})

    outcomes = _ingestor(gmail, sheets, _FakeLlm(), state_path).poll()

    assert outcomes[0].status == IngestStatus.DUPLIKAT
    assert sheets.appended == []
    assert format_email_report(outcomes[0]) is None


def test_ollama_mati_kategori_pakai_default_tetap_tercatat(state_path):
    gmail, sheets = _FakeGmail([_bsi_qris("m1")]), _FakeSheets()

    outcomes = _ingestor(gmail, sheets, _FakeLlm(down=True), state_path).poll()

    assert outcomes[0].status == IngestStatus.TERCATAT
    assert sheets.appended[0].kategori == "Lain-lain / Tak Terduga"


def test_kegagalan_dilaporkan_sekali_lalu_dicoba_lagi_sampai_berhasil(state_path):
    # Arrange
    gmail, sheets, llm = _FakeGmail([_bsi_qris("m1")]), _FakeSheets(), _FakeLlm()
    gmail.fail_ids.add("m1")
    ingestor = _ingestor(gmail, sheets, llm, state_path)

    # Act
    first = ingestor.poll()
    second = ingestor.poll()
    gmail.fail_ids.clear()
    third = ingestor.poll()

    # Assert
    assert [o.status for o in first] == [IngestStatus.GAGAL]
    assert second == []  # still failing, but not re-reported
    assert [o.status for o in third] == [IngestStatus.TERCATAT]
    assert "gagal diproses" in format_email_report(first[0])


def test_pengirim_tanpa_parser_pakai_llm_dan_promo_diabaikan(state_path):
    promo = _message("m3", "promo@bankbaru.co.id", "Promo", "<p>Diskon 50%</p>")
    gmail, sheets = _FakeGmail([promo]), _FakeSheets()

    outcomes = _ingestor(gmail, sheets, _FakeLlm(), state_path).poll()

    assert outcomes[0].status == IngestStatus.BUKAN_TRANSAKSI
    assert sheets.appended == []
    assert format_email_report(outcomes[0]) is None


def test_laporan_telegram_uang_keluar_berisi_nominal_dan_kategori(state_path):
    gmail, sheets, llm = _FakeGmail([_bsi_qris("m1")]), _FakeSheets(), _FakeLlm()

    outcome = _ingestor(gmail, sheets, llm, state_path).poll()[0]
    text = format_email_report(outcome)

    assert text.startswith("💸 Uang Keluar — BSI")
    assert "Rp45.000" in text
    assert "Kategori: Makan & Minum" in text
    assert "WARUNG MAKAN SEDERHANA" in text
