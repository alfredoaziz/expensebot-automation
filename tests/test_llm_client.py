import json
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError

from expensebot.llm.client import ExtractionFailedError, LLMUnavailableError, OllamaClient


def _fake_response(content: str):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


class _FakeCompletions:
    def __init__(self, replies: list[str]):
        self._replies = list(replies)
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        return _fake_response(self._replies.pop(0))


class _UnreachableCompletions:
    def create(self, **_kwargs):
        request = httpx.Request("POST", "http://ollama:11434/v1/chat/completions")
        raise APIConnectionError(request=request)


def _client_with_replies(replies: list[str]) -> tuple[OllamaClient, _FakeCompletions]:
    client = OllamaClient(base_url="http://ollama:11434/v1", api_key="ollama", model="test-model")
    fake_completions = _FakeCompletions(replies)
    client._client.chat.completions = fake_completions
    return client, fake_completions


def test_extract_expense_mem_parsing_json_valid():
    # Arrange
    payload = {"valid": True, "merchant": "Toko A", "kategori": "Lainnya", "total": 1000}
    client, fake = _client_with_replies([json.dumps(payload)])

    # Act
    result = client.extract_expense([b"fake-image-bytes"])

    # Assert
    assert result.merchant == "Toko A"
    assert fake.calls == 1


def test_extract_expense_strip_markdown_code_fence():
    # Arrange
    payload = {"valid": True, "merchant": "Toko B", "kategori": "Lainnya", "total": 2000}
    fenced = f"```json\n{json.dumps(payload)}\n```"
    client, _ = _client_with_replies([fenced])

    # Act
    result = client.extract_expense([b"fake-image-bytes"])

    # Assert
    assert result.merchant == "Toko B"


def test_extract_expense_retry_sekali_lalu_berhasil():
    # Arrange
    payload = {"valid": True, "merchant": "Toko C", "kategori": "Lainnya", "total": 3000}
    client, fake = _client_with_replies(["bukan json", json.dumps(payload)])

    # Act
    result = client.extract_expense([b"fake-image-bytes"])

    # Assert
    assert result.merchant == "Toko C"
    assert fake.calls == 2


def test_extract_expense_gagal_setelah_retry_raise_error():
    # Arrange
    client, _ = _client_with_replies(["bukan json", "masih bukan json"])

    # Act / Assert
    with pytest.raises(ExtractionFailedError):
        client.extract_expense([b"fake-image-bytes"])


def test_extract_expense_ollama_tidak_terhubung_raise_llm_unavailable():
    # Arrange
    client = OllamaClient(base_url="http://ollama:11434/v1", api_key="ollama", model="test-model")
    client._client.chat.completions = _UnreachableCompletions()

    # Act / Assert
    with pytest.raises(LLMUnavailableError):
        client.extract_expense([b"fake-image-bytes"])
