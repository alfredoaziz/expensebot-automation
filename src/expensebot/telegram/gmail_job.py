"""Background loop: polls Gmail for bank emails and reports each new
transaction to the allowed Telegram users."""

from __future__ import annotations

import asyncio
import logging

from telegram import Bot

from expensebot.gmail.ingest import EmailIngestor, IngestOutcome
from expensebot.telegram.formatting import format_email_report

logger = logging.getLogger(__name__)

_POLL_FAILED_MESSAGE = (
    "⚠️ Bot gagal membaca Gmail ({error}). Pencatatan otomatis dari email tertunda — "
    "akan dicoba lagi otomatis. Kalau terus terjadi, cek koneksi internet / izin Google "
    "(jalankan ulang scripts/authorize_google.py) dan `docker compose logs expensebot`."
)
_POLL_RECOVERED_MESSAGE = (
    "✅ Koneksi ke Gmail pulih — pencatatan otomatis dari email jalan lagi."
)


async def _broadcast(bot: Bot, chat_ids: set[int], text: str) -> None:
    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id=chat_id, text=text)
        except Exception:  # noqa: BLE001 — one unreachable chat must not block the rest
            logger.warning(
                "Gagal mengirim laporan email ke chat %s", chat_id, exc_info=True
            )


async def _report(bot: Bot, chat_ids: set[int], outcomes: list[IngestOutcome]) -> None:
    for outcome in outcomes:
        logger.info("Email %s -> %s", outcome.message_id, outcome.status)
        text = format_email_report(outcome)
        if text:
            await _broadcast(bot, chat_ids, text)


async def poll_gmail_forever(
    bot: Bot, ingestor: EmailIngestor, chat_ids: set[int], interval_seconds: int
) -> None:
    """Runs until cancelled. A failing poll (network, expired Google
    token, ...) is reported once, then once more when it recovers — not
    on every retry."""
    is_failing = False
    while True:
        try:
            outcomes = await asyncio.to_thread(ingestor.poll)
        except Exception as exc:  # noqa: BLE001 — keep polling whatever broke
            logger.error("Polling Gmail gagal", exc_info=True)
            if not is_failing:
                await _broadcast(
                    bot, chat_ids, _POLL_FAILED_MESSAGE.format(error=type(exc).__name__)
                )
            is_failing = True
        else:
            if is_failing:
                await _broadcast(bot, chat_ids, _POLL_RECOVERED_MESSAGE)
            is_failing = False
            await _report(bot, chat_ids, outcomes)
        await asyncio.sleep(interval_seconds)
