import datetime as dt

from expensebot.telegram.handlers import _parse_bulan_arg


def test_parse_bulan_arg_format_yyyy_mm():
    # Arrange / Act / Assert
    assert _parse_bulan_arg("2026-08", dt.date(2026, 9, 15)) == (2026, 8)


def test_parse_bulan_arg_nama_bulan_indonesia_case_insensitive():
    # Arrange / Act / Assert
    assert _parse_bulan_arg("agustus", dt.date(2026, 9, 15)) == (2026, 8)
    assert _parse_bulan_arg("Agustus", dt.date(2026, 9, 15)) == (2026, 8)
    assert _parse_bulan_arg("AGUSTUS", dt.date(2026, 9, 15)) == (2026, 8)


def test_parse_bulan_arg_nama_bulan_masa_depan_dianggap_tahun_lalu():
    # Arrange — hari ini September 2026, "Desember" belum terjadi tahun ini
    # jadi diasumsikan Desember 2025.
    today = dt.date(2026, 9, 15)

    # Act
    result = _parse_bulan_arg("desember", today)

    # Assert
    assert result == (2025, 12)


def test_parse_bulan_arg_bulan_berjalan_tetap_tahun_ini():
    # Arrange — bulan yang sama dengan bulan berjalan bukan "masa depan".
    today = dt.date(2026, 9, 15)

    # Act
    result = _parse_bulan_arg("september", today)

    # Assert
    assert result == (2026, 9)


def test_parse_bulan_arg_input_tidak_dikenali_mengembalikan_none():
    # Arrange / Act / Assert
    assert _parse_bulan_arg("bukanbulan", dt.date(2026, 9, 15)) is None
    assert _parse_bulan_arg("2026-13", dt.date(2026, 9, 15)) is None
    assert _parse_bulan_arg("", dt.date(2026, 9, 15)) is None
