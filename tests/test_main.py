import logging

import pytest

from expensebot.google.auth import GoogleAuthNotConfiguredError
from expensebot.main import _with_startup_retry


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    # These tests exercise retry/backoff logic — stub time.sleep so they
    # run instantly instead of actually waiting.
    monkeypatch.setattr("expensebot.main.time.sleep", lambda _seconds: None)


def test_with_startup_retry_mengembalikan_hasil_langsung_kalau_sukses():
    # Arrange / Act
    result = _with_startup_retry(lambda: 42, "test", logging.getLogger("test"))

    # Assert
    assert result == 42


def test_with_startup_retry_coba_lagi_lalu_sukses():
    # Arrange — gagal 2x (mensimulasikan DNS belum siap), sukses di percobaan ke-3.
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] < 3:
            raise ConnectionError("DNS belum siap")
        return "ok"

    # Act
    result = _with_startup_retry(flaky, "test", logging.getLogger("test"))

    # Assert
    assert result == "ok"
    assert calls["count"] == 3


def test_with_startup_retry_menyerah_setelah_percobaan_habis():
    # Arrange
    def always_fails():
        raise ConnectionError("selalu gagal")

    # Act / Assert
    with pytest.raises(ConnectionError):
        _with_startup_retry(always_fails, "test", logging.getLogger("test"))


def test_with_startup_retry_tidak_mengulang_kalau_kredensial_belum_dikonfigurasi():
    # Arrange — file authorized_user.json hilang bukan masalah transient,
    # jadi tidak perlu di-retry (menunggu tidak akan memperbaikinya).
    calls = {"count": 0}

    def missing_creds():
        calls["count"] += 1
        raise GoogleAuthNotConfiguredError("authorized_user.json tidak ditemukan")

    # Act / Assert
    with pytest.raises(GoogleAuthNotConfiguredError):
        _with_startup_retry(missing_creds, "test", logging.getLogger("test"))
    assert calls["count"] == 1
