import datetime as dt

from expensebot.models import ExpenseItem, ExpenseRecord
from expensebot.recap import RecapResult, RekapTahunanResult
from expensebot.telegram.formatting import (
    format_bulan_label,
    format_confirmation,
    format_recap,
    format_recap_tahunan,
)


def test_format_confirmation_menampilkan_tanggal_dan_total():
    # Arrange
    record = ExpenseRecord(
        tanggal=dt.date(2026, 8, 19),
        merchant="Warung Bu Siti",
        kategori="Makan & Minum",
        items=[ExpenseItem(nama="Nasi Goreng", qty=1, harga_satuan=20000, subtotal=20000)],
        total=20000,
    )

    # Act
    text = format_confirmation(record)

    # Assert
    assert "Warung Bu Siti" in text
    assert "Rp20.000" in text
    assert "19 Aug 2026" in text
    assert "Konfirmasi Pengeluaran" in text


def test_format_confirmation_pemasukan_ditandai_berbeda():
    # Arrange
    record = ExpenseRecord(
        tanggal=dt.date(2026, 8, 19),
        merchant="Kantor",
        kategori="Gaji",
        tipe="Pemasukan",
        total=5000000,
    )

    # Act
    text = format_confirmation(record)

    # Assert
    assert "Konfirmasi Pemasukan" in text
    assert "Rp5.000.000" in text


def test_format_confirmation_tanggal_kosong_ditandai():
    # Arrange
    record = ExpenseRecord(
        tanggal=None, merchant="Toko X", kategori="Lainnya", total=5000, butuh_konfirmasi=True
    )

    # Act
    text = format_confirmation(record)

    # Assert
    assert "tidak terbaca" in text
    assert "perlu dikoreksi" in text


def test_format_recap_menampilkan_pemasukan_pengeluaran_dan_saldo():
    # Arrange
    result = RecapResult(
        total_pengeluaran=75000,
        total_pemasukan=100000,
        per_kategori={"Makan & Minum": 50000, "Transportasi": 25000},
        jumlah_transaksi=3,
    )

    # Act
    text = format_recap("harian", result)

    # Assert
    assert "Rp75.000" in text
    assert "Rp100.000" in text
    assert "Rp25.000" in text  # saldo = 100000 - 75000
    assert "Makan & Minum" in text


def test_format_recap_dengan_label_bulan_kustom():
    # Arrange — dipakai untuk /rekap bulanan <bulan lain>, mis. "agustus".
    result = RecapResult(total_pengeluaran=10000, total_pemasukan=0, jumlah_transaksi=1)

    # Act
    text = format_recap("Agustus 2026", result)

    # Assert
    assert "Agustus 2026" in text


def test_format_bulan_label_mengubah_yyyymm_jadi_nama_bulan():
    # Arrange / Act / Assert
    assert format_bulan_label("2026-08") == "Agustus 2026"
    assert format_bulan_label("2026-01") == "Januari 2026"


def test_format_bulan_label_fallback_untuk_string_tak_dikenal():
    # Arrange / Act / Assert
    assert format_bulan_label("bukan-bulan") == "bukan-bulan"


def test_format_recap_tahunan_menampilkan_total_dan_per_bulan():
    # Arrange
    result = RekapTahunanResult(
        tahun=2026,
        per_bulan=[("2026-08", 9121597, 13091935, -3970338), ("2026-09", 3509900, 0, 3509900)],
        total_pemasukan=12631497,
        total_pengeluaran=13091935,
    )

    # Act
    text = format_recap_tahunan(result)

    # Assert
    assert "2026" in text
    assert "Agustus 2026" in text
    assert "September 2026" in text
    assert "Rp12.631.497" in text
    assert "Rp13.091.935" in text


def test_format_recap_tahunan_tahun_kosong_diberi_pesan():
    # Arrange
    result = RekapTahunanResult(tahun=2020)

    # Act
    text = format_recap_tahunan(result)

    # Assert
    assert "belum ada data" in text
