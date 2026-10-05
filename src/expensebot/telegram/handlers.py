"""Telegram update handlers: photo/PDF ingestion (auto-saved, no manual
confirmation step) and the /rekap command.

Dependencies (LLM client, Sheets/Drive repositories, allowed user IDs) are
read from `context.application.bot_data`, populated once in main.py.
"""

from __future__ import annotations

import asyncio
import calendar
import datetime as dt
import hashlib
import logging

from telegram import Update
from telegram.ext import ContextTypes

from expensebot.documents.converter import pdf_to_images, photo_to_images
from expensebot.llm.client import ExtractionFailedError, LLMUnavailableError
from expensebot.models import ExpenseItem, ExpenseRecord
from expensebot.recap import PERIODS, summarize, summarize_tahunan
from expensebot.telegram.formatting import (
    NAMA_BULAN,
    format_confirmation,
    format_recap,
    format_recap_tahunan,
)
from expensebot.timeutil import today_wib

logger = logging.getLogger(__name__)

_NAMA_BULAN_KE_ANGKA = {nama.lower(): angka for angka, nama in NAMA_BULAN.items()}

_REKAP_USAGE = (
    "Gunakan: /rekap harian|mingguan|bulanan [nama bulan], mis. /rekap bulanan agustus, "
    "atau /rekap tahunan [tahun]"
)

_PROGRESS_TICK_SECONDS = 3
_PROGRESS_STEP_PERCENT = 10
_PROGRESS_CAP_PERCENT = 90


async def _tick_progress(processing_msg) -> None:
    """Simulated progress — Ollama's non-streaming API gives no real
    completion ratio, so this is a reassuring estimate, not an exact
    measurement. Ticks up to a cap and holds there until the real result
    (success or error) replaces this message."""
    percent = 0
    while percent < _PROGRESS_CAP_PERCENT:
        await asyncio.sleep(_PROGRESS_TICK_SECONDS)
        percent = min(percent + _PROGRESS_STEP_PERCENT, _PROGRESS_CAP_PERCENT)
        try:
            await processing_msg.edit_text(f"⏳ Membaca struk... {percent}%")
        except Exception:  # noqa: BLE001 — cosmetic update, never fatal
            logger.debug("Progress tick edit failed (non-fatal)", exc_info=True)


async def _extract_expense_with_progress(llm_client, images: list, processing_msg):
    progress_task = asyncio.create_task(_tick_progress(processing_msg))
    try:
        return await asyncio.to_thread(llm_client.extract_expense, images)
    finally:
        progress_task.cancel()
        try:
            await progress_task
        except asyncio.CancelledError:
            pass


def _is_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    allowed_ids: set[int] = context.application.bot_data["allowed_user_ids"]
    user = update.effective_user
    return bool(user and user.id in allowed_ids)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    await update.message.reply_text(
        "Halo! Kirim foto struk atau file PDF — otomatis dicatat sebagai pengeluaran.\n"
        f"{_REKAP_USAGE}."
    )


async def _extract_and_save(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    images: list[bytes],
    file_bytes: bytes,
    mime_type: str,
    filename: str,
) -> None:
    llm_client = context.application.bot_data["llm_client"]
    sheets = context.application.bot_data["sheets_repo"]
    drive = context.application.bot_data["drive_repo"]

    processing_msg = await update.message.reply_text("⏳ Membaca struk... 0%")

    try:
        result = await _extract_expense_with_progress(llm_client, images, processing_msg)
    except LLMUnavailableError:
        await processing_msg.edit_text(
            "❌ Tidak bisa menghubungi Ollama. Pastikan container Ollama jalan dan "
            "model sudah di-pull, lalu coba kirim ulang strukmu."
        )
        return
    except ExtractionFailedError:
        await processing_msg.edit_text(
            "❌ Gagal membaca struk. Coba foto ulang dengan pencahayaan lebih baik."
        )
        return

    if not result.valid:
        await processing_msg.edit_text(
            f"❌ Ini sepertinya bukan struk/nota ({result.alasan or 'tidak dikenali'})."
        )
        return

    record = ExpenseRecord(
        tanggal=result.tanggal,
        merchant=result.merchant,
        tipe=result.tipe,
        kategori=result.kategori,
        items=[ExpenseItem(**item.model_dump()) for item in result.items],
        total=result.total,
        mata_uang=result.mata_uang,
        catatan=result.catatan,
        butuh_konfirmasi=result.butuh_konfirmasi,
        sumber="PDF" if mime_type == "application/pdf" else "Foto",
        dedupe_key=hashlib.sha256(file_bytes).hexdigest()[:16],
    )

    try:
        if record.dedupe_key and await asyncio.to_thread(
            sheets.find_by_dedupe_key, record.dedupe_key
        ):
            await processing_msg.edit_text("⚠️ Transaksi ini sudah tercatat sebelumnya.")
            return

        tanggal = record.tanggal or today_wib()
        drive_link = await asyncio.to_thread(
            drive.upload_receipt, file_bytes, filename, mime_type, tanggal.year, tanggal.month
        )
        record = record.model_copy(update={"drive_link": drive_link})
        await asyncio.to_thread(sheets.append_expense, record)
    except Exception:
        logger.error("Gagal menyimpan transaksi ke Google Sheets/Drive", exc_info=True)
        await processing_msg.edit_text(
            "❌ Struk berhasil dibaca, tapi gagal disimpan ke Google Sheets/Drive "
            "(kemungkinan masalah koneksi atau izin akun Google). Struk TIDAK "
            "tercatat — coba kirim ulang."
        )
        return

    await processing_msg.edit_text(format_confirmation(record) + "\n\n✅ Tersimpan otomatis.")


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    photo = update.message.photo[-1]
    telegram_file = await photo.get_file()
    file_bytes = bytes(await telegram_file.download_as_bytearray())
    images = photo_to_images(file_bytes)
    await _extract_and_save(
        update, context, images, file_bytes, "image/jpeg", f"{photo.file_unique_id}.jpg"
    )


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    document = update.message.document
    if document.mime_type != "application/pdf":
        await update.message.reply_text("Hanya foto atau file PDF yang didukung.")
        return

    telegram_file = await document.get_file()
    file_bytes = bytes(await telegram_file.download_as_bytearray())
    images = pdf_to_images(file_bytes)
    await _extract_and_save(
        update,
        context,
        images,
        file_bytes,
        "application/pdf",
        document.file_name or f"{document.file_unique_id}.pdf",
    )


def _parse_bulan_arg(arg: str, today: dt.date) -> tuple[int, int] | None:
    """Parses a /rekap bulanan month argument: either "yyyy-mm" or an
    Indonesian month name (e.g. "agustus"). A bare month name is assumed
    to fall in the current year, unless that would put it in the future —
    "rekap bulanan januari" asked for in March means this January, not
    one 11 months from now."""
    arg = arg.strip()
    if len(arg) == 7 and arg[4] == "-" and arg[:4].isdigit() and arg[5:].isdigit():
        year, month = int(arg[:4]), int(arg[5:])
        return (year, month) if 1 <= month <= 12 else None

    month = _NAMA_BULAN_KE_ANGKA.get(arg.lower())
    if month is None:
        return None
    year = today.year
    if (year, month) > (today.year, today.month):
        year -= 1
    return year, month


async def _rekap_tahunan(
    update: Update, context: ContextTypes.DEFAULT_TYPE, args: list[str]
) -> None:
    if len(args) > 1:
        try:
            tahun = int(args[1])
        except ValueError:
            await update.message.reply_text(_REKAP_USAGE)
            return
    else:
        tahun = today_wib().year

    sheets = context.application.bot_data["sheets_repo"]
    rows = await asyncio.to_thread(sheets.read_rekap_tahunan_rows)
    result = summarize_tahunan(rows, tahun)
    await update.message.reply_text(format_recap_tahunan(result))


async def _rekap_bulan_lain(
    update: Update, context: ContextTypes.DEFAULT_TYPE, bulan_arg: str, today: dt.date
) -> None:
    parsed = _parse_bulan_arg(bulan_arg, today)
    if parsed is None:
        await update.message.reply_text(
            "Nama bulan tidak dikenali. Gunakan nama bulan (mis. agustus) atau format yyyy-mm."
        )
        return
    year, month = parsed

    sheets = context.application.bot_data["sheets_repo"]
    rows = await asyncio.to_thread(sheets.read_transaksi_rows_for_month, year, month)
    # Every row already belongs to that one month's tab, so the whole
    # month should count — pass its last day as "today" so summarize()'s
    # [start-of-month, today] window covers it entirely.
    last_day = calendar.monthrange(year, month)[1]
    result = summarize(rows, "bulanan", dt.date(year, month, last_day))
    label = f"{NAMA_BULAN[month]} {year}"
    await update.message.reply_text(format_recap(label, result))


async def rekap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return

    args = context.args or []
    period = args[0].lower() if args else "harian"

    if period == "tahunan":
        await _rekap_tahunan(update, context, args)
        return

    if period not in PERIODS:
        await update.message.reply_text(_REKAP_USAGE)
        return

    today = today_wib()

    if period == "bulanan" and len(args) > 1:
        await _rekap_bulan_lain(update, context, args[1], today)
        return

    sheets = context.application.bot_data["sheets_repo"]
    rows = await asyncio.to_thread(sheets.read_transaksi_rows, today)
    result = summarize(rows, period, today)
    await update.message.reply_text(format_recap(period, result))


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global safety net: any exception a handler doesn't catch itself
    lands here instead of silently vanishing into the logs — the user
    always gets a clear reply, whatever broke (Ollama, Google, or
    anything else)."""
    logger.error("Unhandled exception while processing an update", exc_info=context.error)

    if isinstance(update, Update) and update.effective_chat:
        error_name = type(context.error).__name__
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=(
                    f"❌ Terjadi kesalahan tak terduga ({error_name}). Coba lagi — "
                    "kalau terus terjadi, cek `docker compose logs expensebot`."
                ),
            )
        except Exception:
            logger.warning("Gagal mengirim notifikasi error ke user", exc_info=True)
