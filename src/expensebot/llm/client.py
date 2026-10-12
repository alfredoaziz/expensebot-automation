"""Client that talks to Ollama's OpenAI-compatible API to extract
structured expense data from receipt images."""

from __future__ import annotations

import base64
import json
import logging

from openai import APIError, OpenAI
from pydantic import ValidationError

from expensebot.llm.prompts import (
    EMAIL_USER_PROMPT,
    RETRY_PROMPT,
    USER_PROMPT,
    build_category_prompt,
    build_system_prompt,
)
from expensebot.llm.schema import LlmExtractionResult
from expensebot.models import KATEGORI_PEMASUKAN, KATEGORI_PENGELUARAN, TIPE_PEMASUKAN

logger = logging.getLogger(__name__)

# Bank notification bodies are short; this only guards against a huge
# HTML-to-text dump eating the small model's context window.
_MAX_EMAIL_CHARS = 6000


def match_kategori(reply: str, daftar: list[str]) -> str | None:
    """Maps a free-text model reply onto one of `daftar` — exact match
    first, then the longest category name the reply contains."""
    cleaned = reply.strip().strip("\"'`.").casefold()
    for kategori in daftar:
        if cleaned == kategori.casefold():
            return kategori
    contained = [k for k in daftar if k.casefold() in cleaned]
    return max(contained, key=len) if contained else None


class ExtractionFailedError(Exception):
    """Raised when the LLM never returns JSON matching LlmExtractionResult."""


class LLMUnavailableError(Exception):
    """Raised when Ollama can't be reached at all (down, still starting,
    model not pulled yet, or the request timed out)."""


def _image_content(image_bytes: bytes) -> dict:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{encoded}"},
    }


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text.strip()


class OllamaClient:
    def __init__(
        self, base_url: str, api_key: str, model: str, account_holder_name: str = ""
    ) -> None:
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=120)
        self._model = model
        self._system_prompt = build_system_prompt(account_holder_name)

    def check_connection(self) -> None:
        """Lightweight reachability check, used for a clear startup
        warning instead of a silent hang on the first real request."""
        try:
            self._client.models.list()
        except APIError as exc:
            raise LLMUnavailableError(
                "Tidak bisa menghubungi Ollama. Pastikan container Ollama jalan dan "
                "model sudah di-pull."
            ) from exc

    def extract_expense(self, images: list[bytes]) -> LlmExtractionResult:
        user_content = [{"type": "text", "text": USER_PROMPT}] + [
            _image_content(image) for image in images
        ]
        return self._extract(user_content)

    def extract_expense_from_text(self, text: str) -> LlmExtractionResult:
        """Same extraction as extract_expense, for an email body instead of
        an image — used for bank emails that have no dedicated parser."""
        return self._extract(EMAIL_USER_PROMPT + text[:_MAX_EMAIL_CHARS])

    def categorize(self, tipe: str, deskripsi: str) -> str:
        """Picks one category for an already-parsed transaction. Always
        returns a valid category for `tipe` — an unexpected reply falls back
        to the catch-all one. Raises LLMUnavailableError if Ollama is down."""
        if tipe == TIPE_PEMASUKAN:
            daftar, fallback = KATEGORI_PEMASUKAN, "Pemasukan Lainnya"
        else:
            daftar, fallback = KATEGORI_PENGELUARAN, "Lain-lain / Tak Terduga"
        messages = [
            {"role": "system", "content": build_category_prompt(daftar)},
            {"role": "user", "content": deskripsi[:_MAX_EMAIL_CHARS]},
        ]
        return match_kategori(self._complete(messages), daftar) or fallback

    def _extract(self, user_content: str | list[dict]) -> LlmExtractionResult:
        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_content},
        ]

        raw = self._complete(messages)
        result = self._try_parse(raw)
        if result is not None:
            return result

        logger.warning("LLM output did not match schema, retrying once")
        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "user", "content": RETRY_PROMPT})
        raw = self._complete(messages)
        result = self._try_parse(raw)
        if result is not None:
            return result

        raise ExtractionFailedError(
            f"LLM never returned valid JSON. Last output: {raw!r}"
        )

    def _complete(self, messages: list[dict]) -> str:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=0,
            )
        except APIError as exc:
            logger.error("Ollama request failed: %s", exc, exc_info=True)
            raise LLMUnavailableError(
                "Tidak bisa menghubungi Ollama. Pastikan container Ollama jalan dan "
                "model sudah di-pull."
            ) from exc
        return response.choices[0].message.content or ""

    @staticmethod
    def _try_parse(raw: str) -> LlmExtractionResult | None:
        try:
            payload = json.loads(_strip_code_fence(raw))
            return LlmExtractionResult.model_validate(payload)
        except (json.JSONDecodeError, ValidationError):
            return None
