from expensebot.google.sheets import (
    TRANSAKSI_HEADER,
    _dashboard_total_formula,
    _kategori_bulan_terpilih_formula,
    _month_sheet_name,
    _parse_month_sheet_name,
    _row_from_record,
    _shift_month,
    escape_formula_injection,
)


def test_dashboard_total_formula_pengeluaran_seimbang_dan_ikut_tipe_kosong():
    # Arrange / Act
    formula = _dashboard_total_formula("Pengeluaran", include_blank=True)

    # Assert — dibungkus IFERROR (typo di sel bulan tidak boleh error),
    # mengikuti sel selektor bulan $B$1, dan tetap menghitung baris lama
    # yang kolom Tipe-nya kosong (backward compat).
    assert formula.startswith("=IFERROR(")
    assert formula.count("(") == formula.count(")")
    assert "$B$1" in formula
    assert '"Pengeluaran")' in formula
    assert '""),0)' in formula


def test_dashboard_total_formula_pemasukan_tidak_ikut_tipe_kosong():
    # Arrange / Act — baris Pemasukan tidak pernah mendahului kolom Tipe,
    # jadi tidak perlu fallback blank seperti Pengeluaran.
    formula = _dashboard_total_formula("Pemasukan", include_blank=False)

    # Assert
    assert formula.count("(") == formula.count(")")
    assert formula.count("SUMIFS(") == 1
    assert '"Pemasukan")' in formula


def test_kategori_bulan_terpilih_formula_seimbang_dan_mengikuti_selektor():
    # Arrange / Act
    formula = _kategori_bulan_terpilih_formula("J = 'Pengeluaran'")

    # Assert
    assert formula.startswith("=IFERROR(QUERY(")
    assert formula.count("(") == formula.count(")")
    assert "$B$1" in formula


def test_string_diawali_sama_dengan_diberi_prefix_petik():
    # Arrange
    value = "=SUM(A1:A10)"

    # Act
    result = escape_formula_injection(value)

    # Assert
    assert result == "'=SUM(A1:A10)"


def test_string_diawali_plus_minus_at_diberi_prefix_petik():
    # Arrange / Act / Assert
    assert escape_formula_injection("+62812345").startswith("'")
    assert escape_formula_injection("-100").startswith("'")
    assert escape_formula_injection("@mention").startswith("'")


def test_string_normal_tidak_diubah():
    # Arrange
    value = "Warung Makan Bu Siti"

    # Act
    result = escape_formula_injection(value)

    # Assert
    assert result == value


def test_string_kosong_tidak_error():
    # Arrange / Act / Assert
    assert escape_formula_injection("") == ""


def test_month_sheet_name_format_iso_dengan_padding():
    # Arrange / Act / Assert
    assert _month_sheet_name(2026, 9) == "Transaksi 2026-09"
    assert _month_sheet_name(2026, 12) == "Transaksi 2026-12"


def test_parse_month_sheet_name_mengenali_nama_valid():
    # Arrange / Act / Assert
    assert _parse_month_sheet_name("Transaksi 2026-09") == (2026, 9)


def test_parse_month_sheet_name_menolak_nama_lain():
    # Arrange / Act / Assert — legacy tab, Dashboard, Rekap Tahunan, dll
    # tidak boleh salah dikenali sebagai tab bulanan.
    assert _parse_month_sheet_name("Transaksi") is None
    assert _parse_month_sheet_name("Dashboard") is None
    assert _parse_month_sheet_name("Rekap Tahunan") is None


def test_shift_month_mundur_lintas_tahun():
    # Arrange / Act / Assert
    assert _shift_month(2026, 1, -1) == (2025, 12)
    assert _shift_month(2026, 9, -1) == (2026, 8)


def test_shift_month_maju_lintas_tahun():
    # Arrange / Act / Assert
    assert _shift_month(2025, 12, 1) == (2026, 1)


def test_row_from_record_urut_sesuai_header_dan_default_kosong():
    # Arrange — dict dari get_all_records() bisa tidak punya semua kolom
    # (mis. tab lama sebelum kolom "Tipe" ada).
    record = {"Tanggal": "2026-08-19", "Kategori": "Makan & Minum", "Total (Rp)": 30000}

    # Act
    row = _row_from_record(record)

    # Assert
    assert len(row) == len(TRANSAKSI_HEADER)
    assert row[TRANSAKSI_HEADER.index("Tanggal")] == "2026-08-19"
    assert row[TRANSAKSI_HEADER.index("Kategori")] == "Makan & Minum"
    assert row[TRANSAKSI_HEADER.index("Tipe")] == ""
