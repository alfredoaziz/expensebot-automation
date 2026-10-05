from unittest.mock import MagicMock, patch

from expensebot.google.auth import _try_persist_refreshed_token


def test_persist_refreshed_token_swallows_readonly_filesystem_error(tmp_path):
    # Arrange
    token_path = tmp_path / "authorized_user.json"
    creds = MagicMock()
    creds.to_json.return_value = "{}"

    # Act / Assert (must not raise, even though the write fails)
    with patch.object(type(token_path), "write_text", side_effect=OSError("Read-only file system")):
        _try_persist_refreshed_token(token_path, creds)


def test_persist_refreshed_token_writes_when_filesystem_is_writable(tmp_path):
    # Arrange
    token_path = tmp_path / "authorized_user.json"
    creds = MagicMock()
    creds.to_json.return_value = '{"token": "abc"}'

    # Act
    _try_persist_refreshed_token(token_path, creds)

    # Assert
    assert token_path.read_text(encoding="utf-8") == '{"token": "abc"}'
