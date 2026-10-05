"""Entry point: wires up config, Google/LLM clients, and starts polling."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from expensebot.config import load_settings
from expensebot.google.auth import GoogleAuthNotConfiguredError, load_credentials
from expensebot.google.drive import DriveRepository
from expensebot.google.sheets import SheetsRepository
from expensebot.llm.client import LLMUnavailableError, OllamaClient
from expensebot.telegram.handlers import on_document, on_error, on_photo, rekap, start

_T = TypeVar("_T")

_STARTUP_RETRY_ATTEMPTS = 4
_STARTUP_RETRY_INITIAL_DELAY_SECONDS = 2.0


def _with_startup_retry(func: Callable[[], _T], description: str, logger: logging.Logger) -> _T:
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


def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
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

        sheets_repo = _with_startup_retry(_connect_sheets, "Koneksi ke Google Sheets", logger)
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

    application = Application.builder().token(settings.telegram_bot_token).build()
    application.bot_data.update(
        {
            "allowed_user_ids": settings.allowed_user_ids,
            "sheets_repo": sheets_repo,
            "drive_repo": drive_repo,
            "llm_client": llm_client,
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
