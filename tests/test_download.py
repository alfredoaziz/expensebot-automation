import pytest
from telegram.error import TimedOut

from expensebot.telegram import handlers


class _FakeFile:
    async def download_as_bytearray(self) -> bytearray:
        return bytearray(b"image-bytes")


class _FlakyMedia:
    """get_file() times out `failures` times, then succeeds."""

    def __init__(self, failures: int):
        self._failures = failures
        self.calls = 0

    async def get_file(self) -> _FakeFile:
        self.calls += 1
        if self.calls <= self._failures:
            raise TimedOut()
        return _FakeFile()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _instant(_seconds):
        return None

    monkeypatch.setattr(handlers.asyncio, "sleep", _instant)


async def test_download_media_retry_setelah_timeout_lalu_berhasil():
    # Arrange
    media = _FlakyMedia(failures=2)

    # Act
    data = await handlers._download_media(media)

    # Assert
    assert data == b"image-bytes"
    assert media.calls == 3


async def test_download_media_menyerah_setelah_semua_percobaan_timeout():
    # Arrange
    media = _FlakyMedia(failures=99)

    # Act / Assert
    with pytest.raises(TimedOut):
        await handlers._download_media(media)
    assert media.calls == handlers._DOWNLOAD_ATTEMPTS
