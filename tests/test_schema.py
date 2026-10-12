import pytest
from pydantic import ValidationError

from expensebot.llm.schema import LlmExtractionResult


def test_kategori_pengeluaran_tidak_dikenal_dinormalisasi():
    # Arrange
    payload = {
        "valid": True,
        "merchant": "Toko A",
        "kategori": "Kategori Aneh",
        "total": 1000,
    }

    # Act
    result = LlmExtractionResult.model_validate(payload)

    # Assert
    assert result.kategori == "Lain-lain / Tak Terduga"


def test_kategori_valid_dipertahankan():
    # Arrange
    payload = {
        "valid": True,
        "merchant": "Warung B",
        "kategori": "Makan & Minum",
        "total": 25000,
    }

    # Act
    result = LlmExtractionResult.model_validate(payload)

    # Assert
    assert result.kategori == "Makan & Minum"


def test_tipe_default_pengeluaran():
    # Arrange
    payload = {"valid": True, "merchant": "Toko A", "total": 1000}

    # Act
    result = LlmExtractionResult.model_validate(payload)

    # Assert
    assert result.tipe == "Pengeluaran"


def test_kategori_pemasukan_tidak_dikenal_dinormalisasi():
    # Arrange
    payload = {
        "valid": True,
        "merchant": "Kantor",
        "tipe": "Pemasukan",
        "kategori": "Kategori Aneh",
        "total": 5000000,
    }

    # Act
    result = LlmExtractionResult.model_validate(payload)

    # Assert
    assert result.kategori == "Pemasukan Lainnya"


def test_kategori_pengeluaran_ditolak_untuk_tipe_pemasukan():
    # Arrange — "Makan & Minum" is a valid Pengeluaran category, not Pemasukan
    payload = {
        "valid": True,
        "merchant": "Kantor",
        "tipe": "Pemasukan",
        "kategori": "Makan & Minum",
        "total": 5000000,
    }

    # Act
    result = LlmExtractionResult.model_validate(payload)

    # Assert
    assert result.kategori == "Pemasukan Lainnya"


def test_valid_false_tidak_wajib_isi_field_lain():
    # Arrange
    payload = {"valid": False, "alasan": "Bukan struk"}

    # Act
    result = LlmExtractionResult.model_validate(payload)

    # Assert
    assert result.valid is False
    assert result.alasan == "Bukan struk"


def test_field_valid_wajib_ada():
    # Arrange
    payload = {"merchant": "Toko C"}

    # Act / Assert
    with pytest.raises(ValidationError):
        LlmExtractionResult.model_validate(payload)
