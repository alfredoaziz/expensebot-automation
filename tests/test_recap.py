import datetime as dt

from expensebot.recap import summarize, summarize_tahunan


def _row(tanggal: str, kategori: str, total: float, tipe: str = "Pengeluaran") -> dict:
    return {"Tanggal": tanggal, "Kategori": kategori, "Total (Rp)": total, "Tipe": tipe}


def test_summarize_harian_hanya_menghitung_hari_ini():
    # Arrange
    today = dt.date(2026, 8, 19)
    rows = [
        _row("2026-08-19", "Makan & Minum", 50000),
        _row("2026-08-18", "Transportasi", 20000),
    ]

    # Act
    result = summarize(rows, "harian", today=today)

    # Assert
    assert result.total_pengeluaran == 50000
    assert result.jumlah_transaksi == 1
    assert result.per_kategori == {"Makan & Minum": 50000}


def test_summarize_mingguan_mengelompokkan_per_kategori():
    # Arrange
    today = dt.date(2026, 8, 19)  # Wednesday
    rows = [
        _row("2026-08-17", "Makan & Minum", 30000),  # Monday, in week
        _row("2026-08-18", "Makan & Minum", 20000),
        _row("2026-08-10", "Transportasi", 15000),  # previous week
    ]

    # Act
    result = summarize(rows, "mingguan", today=today)

    # Assert
    assert result.total_pengeluaran == 50000
    assert result.per_kategori == {"Makan & Minum": 50000}


def test_summarize_bulanan_mengabaikan_tanggal_tidak_valid():
    # Arrange
    today = dt.date(2026, 8, 19)
    rows = [_row("", "Lain-lain / Tak Terduga", 10000), _row("not-a-date", "Lain-lain", 10000)]

    # Act
    result = summarize(rows, "bulanan", today=today)

    # Assert
    assert result.total_pengeluaran == 0
    assert result.jumlah_transaksi == 0


def test_summarize_memisahkan_pemasukan_dan_menghitung_saldo():
    # Arrange
    today = dt.date(2026, 8, 19)
    rows = [
        _row("2026-08-19", "Gaji", 5000000, tipe="Pemasukan"),
        _row("2026-08-19", "Makan & Minum", 50000, tipe="Pengeluaran"),
    ]

    # Act
    result = summarize(rows, "harian", today=today)

    # Assert
    assert result.total_pemasukan == 5000000
    assert result.total_pengeluaran == 50000
    assert result.saldo == 4950000
    assert result.per_kategori == {"Makan & Minum": 50000}  # income excluded
    assert result.jumlah_transaksi == 2


def test_summarize_tipe_kosong_dianggap_pengeluaran():
    # Arrange — rows saved before the Tipe column existed
    today = dt.date(2026, 8, 19)
    rows = [{"Tanggal": "2026-08-19", "Kategori": "Makan & Minum", "Total (Rp)": 30000}]

    # Act
    result = summarize(rows, "harian", today=today)

    # Assert
    assert result.total_pengeluaran == 30000
    assert result.total_pemasukan == 0


def _rekap_row(bulan: str, pemasukan: float, pengeluaran: float) -> dict:
    return {
        "Bulan": bulan,
        "Pemasukan": pemasukan,
        "Pengeluaran": pengeluaran,
        "Saldo": pemasukan - pengeluaran,
    }


def test_summarize_tahunan_menyaring_hanya_tahun_yang_diminta():
    # Arrange — beberapa tahun tercampur, plus baris "Total" dari sheet asli.
    rows = [
        _rekap_row("2025-12", 1000000, 500000),
        _rekap_row("2026-08", 9121597, 13091935),
        _rekap_row("2026-09", 3509900, 0),
        {"Bulan": "Total", "Pemasukan": 12631497, "Pengeluaran": 13091935, "Saldo": -460438},
    ]

    # Act
    result = summarize_tahunan(rows, 2026)

    # Assert
    assert result.tahun == 2026
    assert [bulan for bulan, *_ in result.per_bulan] == ["2026-08", "2026-09"]
    assert result.total_pemasukan == 9121597 + 3509900
    assert result.total_pengeluaran == 13091935
    assert result.total_saldo == (9121597 + 3509900) - 13091935


def test_summarize_tahunan_tahun_kosong_menghasilkan_nol():
    # Arrange / Act
    result = summarize_tahunan([], 2024)

    # Assert
    assert result.per_bulan == []
    assert result.total_pemasukan == 0
    assert result.total_pengeluaran == 0
    assert result.total_saldo == 0


def test_summarize_tahunan_menerima_angka_string_dari_sheet():
    # Arrange — nilai dari Google Sheets kadang datang sebagai string.
    rows = [{"Bulan": "2026-01", "Pemasukan": "500000", "Pengeluaran": "200000", "Saldo": "300000"}]

    # Act
    result = summarize_tahunan(rows, 2026)

    # Assert
    assert result.per_bulan == [("2026-01", 500000.0, 200000.0, 300000.0)]
