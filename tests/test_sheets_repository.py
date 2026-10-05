"""Exercises SheetsRepository's monthly-tab logic (creation, migration,
dedupe, recap reads) against an in-memory fake spreadsheet — no network,
no real Google Sheets involved."""

from __future__ import annotations

import datetime as dt
import re
from types import SimpleNamespace

import gspread
import pytest

from expensebot.google import sheets as sheets_module
from expensebot.google.sheets import (
    REKAP_TAHUNAN_SHEET,
    TRANSAKSI_HEADER,
    SheetsRepository,
)
from expensebot.models import ExpenseRecord


class FakeWorksheet:
    def __init__(self, title: str, cols: int = 10) -> None:
        self.title = title
        self.id = abs(hash(title)) % 100000
        self.col_count = cols
        self._rows: list[list] = []

    def resize(self, rows: int | None = None, cols: int | None = None) -> None:
        if cols is not None:
            self.col_count = cols

    def append_row(self, row, value_input_option="RAW") -> None:
        self._rows.append(list(row))

    def append_rows(self, rows, value_input_option="RAW") -> None:
        for row in rows:
            self._rows.append(list(row))

    def row_values(self, row_num: int) -> list:
        idx = row_num - 1
        return list(self._rows[idx]) if idx < len(self._rows) else []

    def col_values(self, col_num: int) -> list:
        idx = col_num - 1
        return [row[idx] if idx < len(row) else "" for row in self._rows]

    def get_all_records(self) -> list[dict]:
        if not self._rows:
            return []
        header = self._rows[0]
        return [dict(zip(header, row, strict=False)) for row in self._rows[1:]]

    def update(self, values, range_name="A1", raw=False) -> None:
        # Mirrors a real quirk of the Sheets API: a shorter row only
        # overwrites the cells it actually provides — any trailing cells
        # from a previous write in that same row are left untouched.
        match = re.match(r"^([A-Z]+)(\d+)$", range_name)
        assert match, f"unsupported range in fake: {range_name}"
        start_row = int(match.group(2))
        for offset, row in enumerate(values):
            target_idx = start_row - 1 + offset
            while len(self._rows) <= target_idx:
                self._rows.append([])
            existing = self._rows[target_idx]
            self._rows[target_idx] = list(row) + existing[len(row) :]

    def acell(self, a1: str):
        match = re.match(r"^([A-Z]+)(\d+)$", a1)
        col_letters, row_num = match.group(1), int(match.group(2))
        col_idx = 0
        for ch in col_letters:
            col_idx = col_idx * 26 + (ord(ch) - ord("A") + 1)
        row = self.row_values(row_num)
        value = row[col_idx - 1] if col_idx - 1 < len(row) else None
        return SimpleNamespace(value=value)

    def clear(self) -> None:
        self._rows = []


class FakeSpreadsheet:
    def __init__(self) -> None:
        self.locale = "en_US"
        self._sheets: dict[str, FakeWorksheet] = {}

    def worksheets(self) -> list[FakeWorksheet]:
        return list(self._sheets.values())

    def worksheet(self, title: str) -> FakeWorksheet:
        try:
            return self._sheets[title]
        except KeyError:
            raise gspread.WorksheetNotFound(title) from None

    def add_worksheet(self, title: str, rows: int = 1000, cols: int = 10) -> FakeWorksheet:
        worksheet = FakeWorksheet(title, cols=cols)
        self._sheets[title] = worksheet
        return worksheet

    def del_worksheet(self, worksheet: FakeWorksheet) -> None:
        del self._sheets[worksheet.title]


def _make_repo() -> tuple[SheetsRepository, FakeSpreadsheet]:
    repo = SheetsRepository.__new__(SheetsRepository)
    fake = FakeSpreadsheet()
    repo._spreadsheet = fake
    repo._sheets_api = None  # chart/locale calls are best-effort or skipped
    repo._spreadsheet_id = "fake-spreadsheet-id"
    return repo, fake


def _expense(tanggal: dt.date, total: float = 10000, tipe: str = "Pengeluaran") -> ExpenseRecord:
    return ExpenseRecord(
        tanggal=tanggal,
        merchant="Warung Bu Siti",
        kategori="Makan & Minum",
        total=total,
        tipe=tipe,
        dedupe_key=f"key-{tanggal.isoformat()}-{total}",
    )


@pytest.fixture
def frozen_now(monkeypatch):
    """Pins "now" to 2026-09-15 WIB so tab names in assertions are stable."""
    fixed = dt.datetime(2026, 9, 15, 10, 0, tzinfo=sheets_module.now_wib().tzinfo)
    monkeypatch.setattr(sheets_module, "now_wib", lambda: fixed)
    return fixed


def test_ensure_structure_membuat_tab_bulan_berjalan_dan_rekap(frozen_now):
    # Arrange
    repo, fake = _make_repo()

    # Act
    repo.ensure_structure()

    # Assert
    assert "Transaksi 2026-09" in fake._sheets
    assert fake._sheets["Transaksi 2026-09"].row_values(1) == TRANSAKSI_HEADER
    assert REKAP_TAHUNAN_SHEET in fake._sheets
    assert fake._sheets[REKAP_TAHUNAN_SHEET].row_values(1) == [
        "Bulan",
        "Pemasukan",
        "Pengeluaran",
        "Saldo",
    ]
    assert sheets_module.DASHBOARD_SHEET in fake._sheets


def test_dashboard_punya_selektor_bulan_default_bulan_berjalan(frozen_now):
    # Arrange
    repo, fake = _make_repo()

    # Act
    repo.ensure_structure()

    # Assert — B1 diisi bulan berjalan sebagai default, bisa diganti user
    # untuk lihat bulan lain.
    dashboard = fake._sheets[sheets_module.DASHBOARD_SHEET]
    assert dashboard.row_values(1) == ["Pilih Bulan (yyyy-mm)", "2026-09"]
    assert dashboard.acell("A8").value == "Pemasukan vs Pengeluaran (Bulan Dipilih)"


def test_rekap_tahunan_punya_cukup_kolom_untuk_chart_tren(frozen_now):
    # Arrange — regression: chart tren di-anchor di kolom F (index 5), jadi
    # tab butuh >5 kolom atau Sheets API menolak dengan
    # "columnIndex[5] is after last column in grid".
    repo, fake = _make_repo()

    # Act
    repo.ensure_structure()

    # Assert
    assert fake._sheets[REKAP_TAHUNAN_SHEET].col_count > 5


def test_rekap_tahunan_lama_4_kolom_di_resize_saat_refresh(frozen_now):
    # Arrange — simulasikan tab Rekap Tahunan yang dibuat sebelum fitur
    # chart ada (masih 4 kolom, pas-pasan untuk tabel Bulan/.../Saldo saja).
    repo, fake = _make_repo()
    fake.add_worksheet(REKAP_TAHUNAN_SHEET, cols=4)

    # Act
    repo.ensure_structure()

    # Assert
    assert fake._sheets[REKAP_TAHUNAN_SHEET].col_count > 5


def test_rekap_tahunan_tidak_punya_baris_per_kategori(frozen_now):
    # Arrange — chart Rekap Tahunan sengaja dibatasi ke total Pemasukan vs
    # Pengeluaran per bulan saja, tidak ada breakdown per kategori.
    repo, fake = _make_repo()
    repo.append_expense(_expense(dt.date(2026, 8, 15)))
    repo.append_expense(_expense(dt.date(2026, 9, 5)))

    # Act
    repo.ensure_structure()

    # Assert
    rekap = fake._sheets[REKAP_TAHUNAN_SHEET]
    values = [row[0] for row in rekap._rows if row]
    assert values == ["Bulan", "2026-08", "2026-09", "Total"]


def test_dashboard_recreate_membersihkan_sel_lama(frozen_now):
    # Arrange — regression: _create_dashboard() dulu tidak clear() dulu,
    # jadi sel dari layout sebelumnya (mis. baris yang sekarang lebih
    # pendek) tetap menyisakan nilai lama alih-alih kosong, kadang sampai
    # memblokir spill QUERY (#REF! "would overwrite data").
    repo, fake = _make_repo()
    dashboard = fake.add_worksheet(sheets_module.DASHBOARD_SHEET)
    dashboard.update([["stale label", "sisa nilai lama"]] * 15, "A1")

    # Act — B4 kosong/aneh & A8 tidak cocok layout baru -> _create_dashboard dipanggil.
    repo.ensure_structure()

    # Assert
    assert dashboard.acell("B3").value in (None, "")
    assert dashboard.row_values(3) == ["Ringkasan Bulan Terpilih"]


def test_append_expense_masuk_ke_tab_bulan_sesuai_tanggal_transaksi(frozen_now):
    # Arrange — bot berjalan di bulan September tapi struk yang difoto
    # bertanggal Agustus (mis. struk lama yang baru sempat difoto).
    repo, fake = _make_repo()
    record = _expense(dt.date(2026, 8, 28))

    # Act
    repo.append_expense(record)

    # Assert
    assert "Transaksi 2026-08" in fake._sheets
    assert "Transaksi 2026-09" not in fake._sheets
    rows = fake._sheets["Transaksi 2026-08"].get_all_records()
    assert len(rows) == 1
    assert rows[0]["Merchant"] == "Warung Bu Siti"
    assert rows[0]["Tanggal"] == "2026-08-28"
    # Membuat tab baru harus memicu refresh Rekap Tahunan.
    assert REKAP_TAHUNAN_SHEET in fake._sheets


def test_append_expense_lanjut_ke_bulan_baru_tanpa_restart(frozen_now):
    # Arrange — mensimulasikan bot yang tetap jalan lintas pergantian bulan
    # (Docker restart: unless-stopped, tidak selalu restart di tengah malam).
    repo, fake = _make_repo()
    repo.append_expense(_expense(dt.date(2026, 8, 31)))

    # Act
    repo.append_expense(_expense(dt.date(2026, 9, 15)))

    # Assert
    assert set(fake._sheets["Transaksi 2026-08"].get_all_records()[0].keys()) == set(
        TRANSAKSI_HEADER
    )
    assert len(fake._sheets["Transaksi 2026-08"].get_all_records()) == 1
    assert len(fake._sheets["Transaksi 2026-09"].get_all_records()) == 1


def test_find_by_dedupe_key_menemukan_di_bulan_berjalan_dan_bulan_lalu(frozen_now):
    # Arrange
    repo, _fake = _make_repo()
    this_month = _expense(dt.date(2026, 9, 10))
    last_month = _expense(dt.date(2026, 8, 20))
    repo.append_expense(this_month)
    repo.append_expense(last_month)

    # Act / Assert
    assert repo.find_by_dedupe_key(this_month.dedupe_key) is True
    assert repo.find_by_dedupe_key(last_month.dedupe_key) is True
    assert repo.find_by_dedupe_key("tidak-ada") is False


def test_find_by_dedupe_key_tidak_menjangkau_dua_bulan_lalu(frozen_now):
    # Arrange — di luar current+previous month, dedupe check tidak
    # menjangkau (trade-off yang disengaja, lihat komentar di sheets.py).
    repo, _fake = _make_repo()
    old = _expense(dt.date(2026, 7, 1))
    repo.append_expense(old)

    # Act / Assert
    assert repo.find_by_dedupe_key(old.dedupe_key) is False


def test_read_transaksi_rows_menggabungkan_bulan_berjalan_dan_sebelumnya(frozen_now):
    # Arrange
    repo, _fake = _make_repo()
    repo.append_expense(_expense(dt.date(2026, 8, 30)))
    repo.append_expense(_expense(dt.date(2026, 9, 1)))
    repo.append_expense(_expense(dt.date(2026, 9, 10)))

    # Act
    rows = repo.read_transaksi_rows(dt.date(2026, 9, 15))

    # Assert — cukup untuk /rekap mingguan yang mingguannya melewati batas bulan.
    assert len(rows) == 3


def test_migrasi_tab_transaksi_lama_dipecah_per_bulan(frozen_now):
    # Arrange — tab "Transaksi" gaya lama berisi baris dari dua bulan berbeda.
    repo, fake = _make_repo()
    legacy = fake.add_worksheet("Transaksi")
    legacy.append_row(TRANSAKSI_HEADER)
    legacy.append_row(
        ["2026-07-05", "10:00", "Makan & Minum", "Toko A", "Item", 15000, "Foto", "", "", "", "k1"]
    )
    legacy.append_row(
        ["2026-08-12", "11:00", "Transportasi", "Toko B", "Item", 20000, "Foto", "", "", "", "k2"]
    )

    # Act
    repo.ensure_structure()

    # Assert
    assert "Transaksi" not in fake._sheets
    assert len(fake._sheets["Transaksi 2026-07"].get_all_records()) == 1
    assert len(fake._sheets["Transaksi 2026-08"].get_all_records()) == 1
    rekap_rows = fake._sheets[REKAP_TAHUNAN_SHEET].get_all_records()
    bulan_values = {row["Bulan"] for row in rekap_rows}
    assert {"2026-07", "2026-08", "2026-09"} <= bulan_values


def test_read_transaksi_rows_for_month_bulan_lebih_dari_sebulan_lalu(frozen_now):
    # Arrange — di luar jangkauan read_transaksi_rows (bulan berjalan +
    # bulan lalu saja), ini untuk /rekap bulanan <bulan spesifik>.
    repo, _fake = _make_repo()
    repo.append_expense(_expense(dt.date(2026, 6, 10)))

    # Act
    rows = repo.read_transaksi_rows_for_month(2026, 6)

    # Assert
    assert len(rows) == 1
    assert rows[0]["Tanggal"] == "2026-06-10"


def test_read_transaksi_rows_for_month_belum_ada_tab_mengembalikan_kosong(frozen_now):
    # Arrange
    repo, _fake = _make_repo()

    # Act / Assert
    assert repo.read_transaksi_rows_for_month(2020, 1) == []


def test_read_rekap_tahunan_rows_mengembalikan_baris_apa_adanya(frozen_now):
    # Arrange
    repo, fake = _make_repo()
    repo.ensure_structure()

    # Act
    rows = repo.read_rekap_tahunan_rows()

    # Assert
    assert rows == fake._sheets[REKAP_TAHUNAN_SHEET].get_all_records()


def test_read_rekap_tahunan_rows_belum_ada_tab_mengembalikan_kosong(frozen_now):
    # Arrange
    repo = SheetsRepository.__new__(SheetsRepository)
    repo._spreadsheet = FakeSpreadsheet()
    repo._sheets_api = None

    # Act / Assert
    assert repo.read_rekap_tahunan_rows() == []
