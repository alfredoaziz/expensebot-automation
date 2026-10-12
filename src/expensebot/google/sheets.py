"""Repository for the per-month "Transaksi <yyyy-mm>" tabs, plus the
"Rekap Tahunan" and "Dashboard" tabs of the user's Google Spreadsheet."""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections import defaultdict

import gspread
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from expensebot.models import (
    KATEGORI_PEMASUKAN,
    KATEGORI_PENGELUARAN,
    TIPE_PEMASUKAN,
    TIPE_PENGELUARAN,
    ExpenseRecord,
)
from expensebot.timeutil import now_wib

logger = logging.getLogger(__name__)

# Legacy single-tab name (pre-monthly-split). Any tab still using this
# exact title is migrated into per-month tabs on the next ensure_structure().
_LEGACY_TRANSAKSI_SHEET = "Transaksi"

TRANSAKSI_PREFIX = "Transaksi "
_MONTH_SHEET_RE = re.compile(r"^Transaksi (\d{4})-(\d{2})$")

REKAP_TAHUNAN_SHEET = "Rekap Tahunan"
DASHBOARD_SHEET = "Dashboard"

# "Tipe" sits near the end (not inserted mid-table) so Kategori (C) and
# Total (F) — the columns every Dashboard formula references — never move,
# regardless of when this column was added to an existing spreadsheet.
TRANSAKSI_HEADER = [
    "Tanggal",
    "Waktu",
    "Kategori",
    "Merchant",
    "Item",
    "Total (Rp)",
    "Sumber",
    "Link Drive",
    "Catatan",
    "Tipe",
    "Dedupe Key",
]

_FORMULA_PREFIX_CHARS = ("=", "+", "-", "@")


def _month_sheet_name(year: int, month: int) -> str:
    return f"{TRANSAKSI_PREFIX}{year:04d}-{month:02d}"


def _parse_month_sheet_name(title: str) -> tuple[int, int] | None:
    match = _MONTH_SHEET_RE.match(title)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _parse_tanggal(value: str) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _row_from_record(record: dict) -> list:
    return [record.get(col, "") for col in TRANSAKSI_HEADER]


# Rows saved before the Tipe column existed have it blank — every
# Pengeluaran-scoped formula below must still count them, so each SUMIFS
# criterion is OR'd with a blank check via a second SUMIFS added on top,
# and the QUERY's WHERE clause uses "or J = ''". Pemasukan rows never
# predate the Tipe column (income tracking was added together with it),
# so no blank fallback is needed there.

# Dashboard's month-selector cell — typing a "yyyy-mm" here (e.g. "2026-08")
# switches every figure/chart on the tab to that month, current or past.
_MONTH_CELL = "$B$1"


def _dashboard_month_ref(range_part: str) -> str:
    """A1-style range on whichever "Transaksi <yyyy-mm>" tab the user has
    typed into _MONTH_CELL, resolved live by the spreadsheet itself via
    INDIRECT — so one Dashboard tab can show any month on demand."""
    return f'INDIRECT("\'{TRANSAKSI_PREFIX}"&{_MONTH_CELL}&"\'!{range_part}")'


def _dashboard_total_formula(tipe: str, include_blank: bool) -> str:
    total_col = _dashboard_month_ref("F:F")
    tipe_col = _dashboard_month_ref("J:J")
    base = f'SUMIFS({total_col},{tipe_col},"{tipe}")'
    if include_blank:
        base = f'{base}+SUMIFS({total_col},{tipe_col},"")'
    # IFERROR guards against a typo'd/nonexistent month in the selector
    # cell — shows 0 instead of cascading #REF! into Saldo and the charts.
    return f"=IFERROR({base},0)"


def _kategori_bulan_terpilih_formula(tipe_where: str) -> str:
    """QUERY formula spilling "kategori, total" for one Tipe, scoped to
    whichever month is currently selected in _MONTH_CELL."""
    data_range = _dashboard_month_ref("A:J")
    return (
        f'=IFERROR(QUERY({data_range},"select C, sum(F) where ({tipe_where}) '
        'group by C label sum(F) \'\'",1),"")'
    )


_PENGELUARAN_KATEGORI_FORMULA = _kategori_bulan_terpilih_formula(
    f"J = '{TIPE_PENGELUARAN}' or J = ''"
)
_PEMASUKAN_KATEGORI_FORMULA = _kategori_bulan_terpilih_formula(
    f"J = '{TIPE_PEMASUKAN}'"
)

# Dashboard row layout (0-indexed, matches the Sheets API). Row 0 (sheet
# row 1) holds the month-selector cell (_MONTH_CELL). Each QUERY above
# spills one row per distinct category — reserve the maximum possible (the
# full category list length) so the next static section never gets
# overwritten by a wider-than-expected spill.
_BALANCE_START_ROW = 8  # row 9: "Pemasukan" | =B5 ; row 10: "Pengeluaran" | =B4
_BALANCE_END_ROW = 10
_PENGELUARAN_KATEGORI_START_ROW = 12  # row 13
_PENGELUARAN_KATEGORI_END_ROW = _PENGELUARAN_KATEGORI_START_ROW + len(
    KATEGORI_PENGELUARAN
)
_PEMASUKAN_KATEGORI_LABEL_ROW = _PENGELUARAN_KATEGORI_END_ROW + 1  # one blank row after
_PEMASUKAN_KATEGORI_START_ROW = _PEMASUKAN_KATEGORI_LABEL_ROW + 1
_PEMASUKAN_KATEGORI_END_ROW = _PEMASUKAN_KATEGORI_START_ROW + len(KATEGORI_PEMASUKAN)


def _range_source(
    sheet_id: int, start_row: int, end_row: int, start_col: int, end_col: int
) -> dict:
    return {
        "sourceRange": {
            "sources": [
                {
                    "sheetId": sheet_id,
                    "startRowIndex": start_row,
                    "endRowIndex": end_row,
                    "startColumnIndex": start_col,
                    "endColumnIndex": end_col,
                }
            ]
        }
    }


def _pie_chart_request(
    sheet_id: int, title: str, start_row: int, end_row: int, anchor_row: int
) -> dict:
    return {
        "addChart": {
            "chart": {
                "spec": {
                    "title": title,
                    "pieChart": {
                        "legendPosition": "RIGHT_LEGEND",
                        "domain": _range_source(sheet_id, start_row, end_row, 0, 1),
                        "series": _range_source(sheet_id, start_row, end_row, 1, 2),
                    },
                },
                "position": {
                    "overlayPosition": {
                        "anchorCell": {
                            "sheetId": sheet_id,
                            "rowIndex": anchor_row,
                            "columnIndex": 7,
                        }
                    }
                },
            }
        }
    }


def _balance_chart_request(dashboard_sheet_id: int, anchor_row: int) -> dict:
    domain = _range_source(
        dashboard_sheet_id, _BALANCE_START_ROW, _BALANCE_END_ROW, 0, 1
    )
    series = _range_source(
        dashboard_sheet_id, _BALANCE_START_ROW, _BALANCE_END_ROW, 1, 2
    )
    return {
        "addChart": {
            "chart": {
                "spec": {
                    "title": "Pemasukan vs Pengeluaran (Bulan Dipilih)",
                    "basicChart": {
                        "chartType": "COLUMN",
                        "legendPosition": "NO_LEGEND",
                        "domains": [{"domain": domain}],
                        "series": [{"series": series, "targetAxis": "LEFT_AXIS"}],
                    },
                },
                "position": {
                    "overlayPosition": {
                        "anchorCell": {
                            "sheetId": dashboard_sheet_id,
                            "rowIndex": anchor_row,
                            "columnIndex": 7,
                        }
                    }
                },
            }
        }
    }


def _trend_chart_request(sheet_id: int, months_end_row: int) -> dict:
    """Column chart comparing Pemasukan vs Pengeluaran across every month
    row in Rekap Tahunan (rows 1..months_end_row, 0-indexed, i.e. sheet
    rows 2..months_end_row+1 — the Total row is intentionally excluded)."""
    domain = _range_source(sheet_id, 1, months_end_row, 0, 1)
    pemasukan_series = _range_source(sheet_id, 1, months_end_row, 1, 2)
    pengeluaran_series = _range_source(sheet_id, 1, months_end_row, 2, 3)
    return {
        "addChart": {
            "chart": {
                "spec": {
                    "title": "Pemasukan vs Pengeluaran per Bulan",
                    "basicChart": {
                        "chartType": "COLUMN",
                        "legendPosition": "BOTTOM_LEGEND",
                        "domains": [{"domain": domain}],
                        "series": [
                            {"series": pemasukan_series, "targetAxis": "LEFT_AXIS"},
                            {"series": pengeluaran_series, "targetAxis": "LEFT_AXIS"},
                        ],
                    },
                },
                "position": {
                    "overlayPosition": {
                        "anchorCell": {
                            "sheetId": sheet_id,
                            "rowIndex": 0,
                            "columnIndex": 5,
                        }
                    }
                },
            }
        }
    }


def escape_formula_injection(value: str) -> str:
    """Prefix values that Google Sheets would otherwise interpret as a
    formula, so OCR'd merchant/item text can never execute a formula."""
    if value and value[0] in _FORMULA_PREFIX_CHARS:
        return f"'{value}"
    return value


class SheetsRepository:
    def __init__(self, credentials: Credentials, spreadsheet_id: str) -> None:
        self._gc = gspread.authorize(credentials)
        self._spreadsheet = self._gc.open_by_key(spreadsheet_id)
        self._sheets_api = build("sheets", "v4", credentials=credentials)
        self._spreadsheet_id = spreadsheet_id

    def ensure_structure(self) -> None:
        self._ensure_locale()
        self._migrate_legacy_transaksi()

        now = now_wib()
        self._ensure_month_sheet(now.year, now.month)
        self._refresh_rekap_tahunan()

        existing = {ws.title for ws in self._spreadsheet.worksheets()}
        if DASHBOARD_SHEET not in existing:
            dashboard = self._spreadsheet.add_worksheet(
                DASHBOARD_SHEET, rows=50, cols=10
            )
            self._create_dashboard(dashboard)
        else:
            # Recover from a previous run that created the tab but left it
            # empty, broken, or on an outdated layout (formulas written
            # under the wrong locale, the gspread argument-order bug, or a
            # layout predating income tracking / the monthly-tab split /
            # the month-selector — all fixed above, but already-written
            # cells need to be re-written to pick it up).
            dashboard = self._spreadsheet.worksheet(DASHBOARD_SHEET)
            b4 = dashboard.acell("B4").value
            # A8 only holds this exact label in the current layout.
            a8 = dashboard.acell("A8").value
            if (
                b4 in (None, "", "#ERROR!", "#VALUE!")
                or a8 != "Pemasukan vs Pengeluaran (Bulan Dipilih)"
            ):
                self._create_dashboard(dashboard)

    def _migrate_legacy_transaksi(self) -> None:
        """One-time upgrade: splits a pre-monthly-split "Transaksi" tab's
        rows into "Transaksi <yyyy-mm>" tabs by their own Tanggal, then
        removes it. No-ops once that tab is gone."""
        existing = {ws.title for ws in self._spreadsheet.worksheets()}
        if _LEGACY_TRANSAKSI_SHEET not in existing:
            return

        legacy = self._spreadsheet.worksheet(_LEGACY_TRANSAKSI_SHEET)
        records = legacy.get_all_records()
        fallback_year, fallback_month = now_wib().year, now_wib().month

        grouped: dict[tuple[int, int], list[list]] = defaultdict(list)
        for record in records:
            tanggal = _parse_tanggal(str(record.get("Tanggal", "")))
            key = (
                (tanggal.year, tanggal.month)
                if tanggal
                else (fallback_year, fallback_month)
            )
            grouped[key].append(_row_from_record(record))

        for (year, month), rows in sorted(grouped.items()):
            worksheet, _created = self._ensure_month_sheet(year, month)
            if rows:
                worksheet.append_rows(rows, value_input_option="USER_ENTERED")

        self._spreadsheet.del_worksheet(legacy)
        logger.info(
            "Migrasi %d baris dari tab 'Transaksi' lama ke %d tab bulanan selesai.",
            len(records),
            len(grouped),
        )

    def _ensure_month_sheet(
        self, year: int, month: int
    ) -> tuple[gspread.Worksheet, bool]:
        """Returns (worksheet, created) — created is True the first time
        this month's tab is made, so callers can decide whether the
        Rekap Tahunan tab needs a new row."""
        name = _month_sheet_name(year, month)
        try:
            return self._spreadsheet.worksheet(name), False
        except gspread.WorksheetNotFound:
            worksheet = self._spreadsheet.add_worksheet(
                name, rows=1000, cols=len(TRANSAKSI_HEADER)
            )
            worksheet.append_row(TRANSAKSI_HEADER, value_input_option="RAW")
            return worksheet, True

    def _ensure_locale(self) -> None:
        """Pins the spreadsheet locale to en_US so the comma-separated
        formulas below (SUMIFS(...), QUERY(...)) always parse — many
        regional locales (including id_ID) expect ';' as the argument
        separator instead, which turns these into #ERROR!."""
        if self._spreadsheet.locale == "en_US":
            return
        request = {
            "updateSpreadsheetProperties": {
                "properties": {"locale": "en_US"},
                "fields": "locale",
            }
        }
        self._sheets_api.spreadsheets().batchUpdate(
            spreadsheetId=self._spreadsheet_id, body={"requests": [request]}
        ).execute()

    def append_expense(self, record: ExpenseRecord) -> None:
        now = now_wib()
        target_date = record.tanggal or now.date()
        worksheet, created = self._ensure_month_sheet(
            target_date.year, target_date.month
        )
        item_ringkas = (
            ", ".join(f"{i.nama} x{i.qty:g}" for i in record.items) or record.merchant
        )
        row = [
            record.tanggal.isoformat() if record.tanggal else "",
            record.waktu or now.strftime("%H:%M"),
            record.kategori,
            escape_formula_injection(record.merchant),
            escape_formula_injection(item_ringkas),
            record.total,
            record.sumber,
            record.drive_link or "",
            escape_formula_injection(record.catatan or ""),
            record.tipe,
            record.dedupe_key or "",
        ]
        worksheet.append_row(row, value_input_option="USER_ENTERED")
        if created:
            self._refresh_rekap_tahunan()

    def find_by_dedupe_key(self, dedupe_key: str) -> bool:
        # Checks the current + previous month's tabs only — a receipt sent
        # more than a month after the original is not expected to be a
        # duplicate re-send, and scanning every historical tab would grow
        # unbounded as months pile up.
        today = now_wib().date()
        for year, month in (
            (today.year, today.month),
            _shift_month(today.year, today.month, -1),
        ):
            try:
                worksheet = self._spreadsheet.worksheet(_month_sheet_name(year, month))
            except gspread.WorksheetNotFound:
                continue
            column = worksheet.col_values(len(TRANSAKSI_HEADER))
            if dedupe_key in column:
                return True
        return False

    def read_transaksi_rows(self, today: dt.date | None = None) -> list[dict]:
        """Rows from the current + previous month's tabs — enough to cover
        "harian"/"mingguan"/"bulanan" recaps, since a week can span at most
        two calendar months."""
        today = today or now_wib().date()
        months = {(today.year, today.month), _shift_month(today.year, today.month, -1)}
        rows: list[dict] = []
        for year, month in sorted(months):
            try:
                worksheet = self._spreadsheet.worksheet(_month_sheet_name(year, month))
            except gspread.WorksheetNotFound:
                continue
            rows.extend(worksheet.get_all_records())
        return rows

    def read_transaksi_rows_for_month(self, year: int, month: int) -> list[dict]:
        """Rows from exactly one month's tab — for /rekap on a month other
        than the current/previous one, which read_transaksi_rows doesn't
        reach. Empty list if that month was never tracked."""
        try:
            worksheet = self._spreadsheet.worksheet(_month_sheet_name(year, month))
        except gspread.WorksheetNotFound:
            return []
        return worksheet.get_all_records()

    def read_rekap_tahunan_rows(self) -> list[dict]:
        """Rows from the Rekap Tahunan tab (Bulan/Pemasukan/Pengeluaran/
        Saldo, one row per tracked month, plus a Total row) — already
        aggregated by Sheets itself, so /rekap tahunan doesn't need to
        re-sum every month tab in Python."""
        try:
            worksheet = self._spreadsheet.worksheet(REKAP_TAHUNAN_SHEET)
        except gspread.WorksheetNotFound:
            return []
        return worksheet.get_all_records()

    def _refresh_rekap_tahunan(self) -> None:
        months = sorted(
            parsed
            for ws in self._spreadsheet.worksheets()
            if (parsed := _parse_month_sheet_name(ws.title)) is not None
        )

        existing = {ws.title for ws in self._spreadsheet.worksheets()}
        # 10 cols (not just the 4 the table itself needs) so the trend
        # chart — anchored past column D — has room; matches Dashboard's
        # own column count.
        if REKAP_TAHUNAN_SHEET not in existing:
            worksheet = self._spreadsheet.add_worksheet(
                REKAP_TAHUNAN_SHEET, rows=max(len(months) + 10, 20), cols=10
            )
        else:
            worksheet = self._spreadsheet.worksheet(REKAP_TAHUNAN_SHEET)
            worksheet.clear()
            if worksheet.col_count < 10:
                worksheet.resize(cols=10)

        rows: list[list] = [["Bulan", "Pemasukan", "Pengeluaran", "Saldo"]]
        for year, month in months:
            row_num = len(rows) + 1
            name = _month_sheet_name(year, month)
            pemasukan = f"=SUMIFS('{name}'!F:F,'{name}'!J:J,\"{TIPE_PEMASUKAN}\")"
            pengeluaran = (
                f"=SUMIFS('{name}'!F:F,'{name}'!J:J,\"{TIPE_PENGELUARAN}\")"
                f"+SUMIFS('{name}'!F:F,'{name}'!J:J,\"\")"
            )
            saldo = f"=B{row_num}-C{row_num}"
            rows.append([f"{year:04d}-{month:02d}", pemasukan, pengeluaran, saldo])

        # 0-indexed exclusive end of the month rows (header is row 0) —
        # captured before the Total row is appended, so the trend chart's
        # domain covers months only.
        trend_end_row = len(rows)

        if not months:
            worksheet.update(rows, "A1", raw=False)
            try:
                self._delete_existing_charts(worksheet.id)
            except Exception:
                logger.warning(
                    "Gagal membersihkan chart Rekap Tahunan (non-fatal)", exc_info=True
                )
            return

        total_row = len(rows) + 1
        rows.append(
            [
                "Total",
                f"=SUM(B2:B{total_row - 1})",
                f"=SUM(C2:C{total_row - 1})",
                f"=SUM(D2:D{total_row - 1})",
            ]
        )

        worksheet.update(rows, "A1", raw=False)
        self._add_rekap_tahunan_charts(worksheet.id, trend_end_row)

    def _add_rekap_tahunan_charts(self, sheet_id: int, trend_end_row: int) -> None:
        # Best-effort: chart placement is a nice-to-have, never block startup on it.
        try:
            self._delete_existing_charts(sheet_id)
            requests = [_trend_chart_request(sheet_id, trend_end_row)]
            self._sheets_api.spreadsheets().batchUpdate(
                spreadsheetId=self._spreadsheet_id, body={"requests": requests}
            ).execute()
        except Exception:
            logger.warning(
                "Gagal membuat chart Rekap Tahunan (non-fatal)", exc_info=True
            )

    def _create_dashboard(self, worksheet) -> None:
        default_month = f"{now_wib().year:04d}-{now_wib().month:02d}"
        # Clear first — update() only overwrites the cells its `values`
        # matrix actually covers, so a shorter row (e.g. a single-column
        # label) leaves whatever was in the neighbouring cell from a prior
        # layout untouched. Without this, stale leftovers show up as ghost
        # values, or block a QUERY's spill with a #REF! "would overwrite
        # data" error.
        worksheet.clear()
        # RAW, not USER_ENTERED — Sheets otherwise auto-parses a bare
        # "yyyy-mm" string as a DATE (still *displayed* as "2026-09", but
        # stored as a date serial number), which silently breaks the &$B$1&
        # string concatenation every INDIRECT formula below depends on.
        worksheet.update([["Pilih Bulan (yyyy-mm)", default_month]], "A1", raw=True)
        # gspread 6.x's update() takes (values, range_name) — passing them
        # reversed (as gspread 5.x required) silently breaks this. A bare
        # cell anchor (no end bound) lets gspread size the range to match
        # `values` instead of us having to declare it (and get it wrong).
        worksheet.update(
            [
                [""],
                ["Ringkasan Bulan Terpilih"],
                [
                    "Pengeluaran",
                    _dashboard_total_formula(TIPE_PENGELUARAN, include_blank=True),
                ],
                [
                    "Pemasukan",
                    _dashboard_total_formula(TIPE_PEMASUKAN, include_blank=False),
                ],
                ["Saldo", "=B5-B4"],
                [""],
                ["Pemasukan vs Pengeluaran (Bulan Dipilih)"],
                ["Pemasukan", "=B5"],
                ["Pengeluaran", "=B4"],
                [""],
                ["Per Kategori Pengeluaran (Bulan Dipilih)"],
                [_PENGELUARAN_KATEGORI_FORMULA],
            ],
            "A2",
            raw=False,
        )
        worksheet.update(
            [["Per Kategori Pemasukan (Bulan Dipilih)"], [_PEMASUKAN_KATEGORI_FORMULA]],
            f"A{_PEMASUKAN_KATEGORI_LABEL_ROW + 1}",
            raw=False,
        )
        self._add_charts(worksheet.id)

    def _add_charts(self, dashboard_sheet_id: int) -> None:
        # Best-effort: chart placement is a nice-to-have, never block startup on it.
        try:
            self._delete_existing_charts(dashboard_sheet_id)
            requests = [
                _pie_chart_request(
                    dashboard_sheet_id,
                    "Pengeluaran per Kategori (Bulan Dipilih)",
                    _PENGELUARAN_KATEGORI_START_ROW,
                    _PENGELUARAN_KATEGORI_END_ROW,
                    anchor_row=1,
                ),
                _pie_chart_request(
                    dashboard_sheet_id,
                    "Pemasukan per Kategori (Bulan Dipilih)",
                    _PEMASUKAN_KATEGORI_START_ROW,
                    _PEMASUKAN_KATEGORI_END_ROW,
                    anchor_row=_PEMASUKAN_KATEGORI_LABEL_ROW,
                ),
                _balance_chart_request(
                    dashboard_sheet_id, anchor_row=_PEMASUKAN_KATEGORI_END_ROW + 2
                ),
            ]
            self._sheets_api.spreadsheets().batchUpdate(
                spreadsheetId=self._spreadsheet_id, body={"requests": requests}
            ).execute()
        except Exception:
            logger.warning("Gagal membuat chart Dashboard (non-fatal)", exc_info=True)

    def _delete_existing_charts(self, sheet_id: int) -> None:
        """addChart always creates a new chart object — without this,
        re-running the tab's builder (e.g. on a layout upgrade, or a new
        month arriving in Rekap Tahunan) would pile up duplicate charts."""
        meta = (
            self._sheets_api.spreadsheets()
            .get(
                spreadsheetId=self._spreadsheet_id,
                fields="sheets(properties.sheetId,charts.chartId)",
            )
            .execute()
        )
        chart_ids = [
            chart["chartId"]
            for sheet in meta.get("sheets", [])
            if sheet["properties"]["sheetId"] == sheet_id
            for chart in sheet.get("charts", [])
        ]
        if not chart_ids:
            return
        requests = [{"deleteEmbeddedObject": {"objectId": cid}} for cid in chart_ids]
        self._sheets_api.spreadsheets().batchUpdate(
            spreadsheetId=self._spreadsheet_id, body={"requests": requests}
        ).execute()
