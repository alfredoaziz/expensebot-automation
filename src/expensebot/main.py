"""Entry point: wires up config, Google/LLM clients, and starts polling."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from typing import TypeVar

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from expensebot.config import Settings, load_settings
from expensebot.gmail.client import GmailRepository
from expensebot.gmail.ingest import EmailIngestor
from expensebot.gmail.state import ProcessedStore
from expensebot.google.auth import (
    GMAIL_SCOPE,
    GoogleAuthNotConfiguredError,
    has_scope,
    load_credentials,
)
from expensebot.google.drive import DriveRepository
from expensebot.google.sheets import SheetsRepository
from expensebot.llm.client import LLMUnavailableError, OllamaClient
from expensebot.telegram.gmail_job import poll_gmail_forever
from expensebot.telegram.handlers import on_document, on_error, on_photo, rekap, start

_T = TypeVar("_T")

_STARTUP_RETRY_ATTEMPTS = 4
_STARTUP_RETRY_INITIAL_DELAY_SECONDS = 2.0

_TELEGRAM_CONNECT_TIMEOUT_SECONDS = 20.0
_TELEGRAM_READ_TIMEOUT_SECONDS = 60.0


def _with_startup_retry(
    func: Callable[[], _T], description: str, logger: logging.Logger
) -> _T:
    """Retries a startup network call with exponential backoff before
    giving up. Docker containers can start before the host's networking
    (DNS in particular) is actually ready — especially right after Docker
    Desktop/Windows boot — so a transient failure here shouldn't crash the
    whole bot; it should just wait a few seconds and try again."""
    delay = _STARTUP_RETRY_INITIAL_DELAY_SECONDS
    for attempt in range(1, _STARTUP_RETRY_ATTEMPTS + 1):
        try:
            return func()
        except GoogleAuthNotConfiguredError:
            raise  # a missing credentials file — retrying can't fix that
        except Exception:
            if attempt == _STARTUP_RETRY_ATTEMPTS:
                raise
            logger.warning(
                "%s gagal (percobaan %d/%d) — kemungkinan jaringan/DNS belum siap. "
                "Coba lagi dalam %.0f detik...",
                description,
                attempt,
                _STARTUP_RETRY_ATTEMPTS,
                delay,
                exc_info=True,
            )
            time.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")  # loop always returns or raises above


def _build_gmail_ingestor(
    settings: Settings, credentials, sheets_repo, llm_client, logger: logging.Logger
) -> EmailIngestor | None:
    """V2 email ingestion is optional: the bot keeps serving photo/PDF
    uploads and /rekap even when Gmail isn't authorized yet."""
    if not settings.gmail_enabled:
        logger.info(
            "Pencatatan otomatis dari Gmail dinonaktifkan (GMAIL_ENABLED=false)."
        )
        return None
    if not has_scope(credentials, GMAIL_SCOPE):
        logger.warning(
            "⚠️ Token Google belum punya izin baca Gmail — pencatatan otomatis dari email "
            "NONAKTIF. Aktifkan Gmail API di Google Cloud Console, lalu jalankan ulang "
            "`python scripts/authorize_google.py` di host dan restart bot."
        )
        return None
    if not settings.account_holder_name:
        logger.warning(
            "⚠️ ACCOUNT_HOLDER_NAME kosong — transfer antar rekening sendiri tidak bisa "
            "dikenali dan akan tercatat sebagai pemasukan/pengeluaran biasa."
        )
    logger.info(
        "Pencatatan otomatis dari Gmail aktif (query: %s)", settings.gmail_query
    )
    return EmailIngestor(
        gmail=GmailRepository(credentials),
        sheets=sheets_repo,
        llm_client=llm_client,
        store=ProcessedStore(settings.gmail_state_path),
        query=settings.gmail_query,
        account_holder_name=settings.account_holder_name,
        catat_transfer_sendiri=settings.gmail_catat_transfer_sendiri,
    )


async def _start_gmail_polling(application: Application) -> None:
    ingestor = application.bot_data.get("gmail_ingestor")
    if ingestor is None:
        return
    application.bot_data["gmail_task"] = asyncio.create_task(
        poll_gmail_forever(
            application.bot,
            ingestor,
            application.bot_data["allowed_user_ids"],
            application.bot_data["gmail_poll_interval_seconds"],
        )
    )


async def _stop_gmail_polling(application: Application) -> None:
    task = application.bot_data.get("gmail_task")
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs each request's full URL at INFO — for Telegram calls that
    # URL embeds the bot token (https://api.telegram.org/bot<TOKEN>/...),
    # so this would otherwise leak it into every log line. WARNING+ still
    # surfaces real connection failures.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logger = logging.getLogger(__name__)

    # credentials.refresh() below (inside load_credentials, when the cached
    # access token has expired) and every Sheets/Drive call after it hit
    # Google's servers over the network — retried so a container that
    # starts before Docker/Windows networking is actually ready (a real
    # race right after boot or a Docker Desktop restart) doesn't crash on
    # a transient DNS failure instead of just waiting a few seconds.
    try:
        credentials = _with_startup_retry(
            lambda: load_credentials(settings.google_credentials_dir),
            "Memuat/refresh kredensial Google",
            logger,
        )
    except GoogleAuthNotConfiguredError as exc:
        logger.error("❌ Google belum ter-otorisasi: %s", exc)
        raise
    except Exception:
        logger.error(
            "❌ Gagal memuat kredensial Google — cek isi credentials/authorized_user.json "
            "(jalankan scripts/authorize_google.py ulang kalau perlu) dan koneksi internet.",
            exc_info=True,
        )
        raise

    try:

        def _connect_sheets() -> SheetsRepository:
            repo = SheetsRepository(credentials, settings.google_spreadsheet_id)
            repo.ensure_structure()
            return repo

        sheets_repo = _with_startup_retry(
            _connect_sheets, "Koneksi ke Google Sheets", logger
        )
    except Exception:
        logger.error(
            "❌ Gagal terhubung ke Google Sheets. Cek GOOGLE_SPREADSHEET_ID di .env "
            "(harus ID saja dari URL spreadsheet, bukan URL lengkap), koneksi internet, dan "
            "pastikan akun Google yang login di scripts/authorize_google.py punya akses ke "
            "spreadsheet itu.",
            exc_info=True,
        )
        raise

    try:
        drive_repo = _with_startup_retry(
            lambda: DriveRepository(credentials, settings.google_drive_folder_id),
            "Koneksi ke Google Drive",
            logger,
        )
    except Exception:
        logger.error(
            "❌ Gagal menyiapkan Google Drive. Cek GOOGLE_DRIVE_FOLDER_ID di .env "
            "(harus ID saja dari URL folder, bukan URL lengkap).",
            exc_info=True,
        )
        raise

    llm_client = OllamaClient(
        settings.llm_base_url,
        settings.llm_api_key,
        settings.llm_model,
        settings.account_holder_name,
    )
    try:
        llm_client.check_connection()
    except LLMUnavailableError:
        logger.warning(
            "⚠️ Ollama belum bisa dihubungi di %s (model '%s'). Bot tetap jalan, tapi "
            "pembacaan struk akan gagal sampai container Ollama aktif dan modelnya "
            "sudah di-pull (`docker exec -it ollama ollama pull %s`).",
            settings.llm_base_url,
            settings.llm_model,
            settings.llm_model,
        )

    # python-telegram-bot defaults every Bot API call to a 5s read timeout.
    # Behind a TLS-inspecting proxy (see Dockerfile's CA bundle) a getFile /
    # file download routinely takes longer than that, which surfaced as
    # "TimedOut" on photo uploads — so give each call real headroom.
    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .connect_timeout(_TELEGRAM_CONNECT_TIMEOUT_SECONDS)
        .read_timeout(_TELEGRAM_READ_TIMEOUT_SECONDS)
        .write_timeout(_TELEGRAM_READ_TIMEOUT_SECONDS)
        .pool_timeout(_TELEGRAM_CONNECT_TIMEOUT_SECONDS)
        .get_updates_connect_timeout(_TELEGRAM_CONNECT_TIMEOUT_SECONDS)
        .get_updates_read_timeout(_TELEGRAM_READ_TIMEOUT_SECONDS)
        .get_updates_write_timeout(_TELEGRAM_READ_TIMEOUT_SECONDS)
        .get_updates_pool_timeout(_TELEGRAM_CONNECT_TIMEOUT_SECONDS)
        .post_init(_start_gmail_polling)
        .post_stop(_stop_gmail_polling)
        .build()
    )
    application.bot_data.update(
        {
            "allowed_user_ids": settings.allowed_user_ids,
            "sheets_repo": sheets_repo,
            "drive_repo": drive_repo,
            "llm_client": llm_client,
            "gmail_ingestor": _build_gmail_ingestor(
                settings, credentials, sheets_repo, llm_client, logger
            ),
            "gmail_poll_interval_seconds": settings.gmail_poll_interval_seconds,
        }
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("rekap", rekap))
    application.add_handler(MessageHandler(filters.PHOTO, on_photo))
    application.add_handler(MessageHandler(filters.Document.PDF, on_document))
    application.add_error_handler(on_error)

    logger.info("Bot dimulai (polling)...")
    application.run_polling()


if __name__ == "__main__":
    main()
