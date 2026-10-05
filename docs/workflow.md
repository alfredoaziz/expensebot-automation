# Workflow: from receipt to spreadsheet

## Happy path

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant T as Telegram
    participant H as handlers.py
    participant C as converter.py
    participant L as Ollama (vision LLM)
    participant S as Google Sheets
    participant D as Google Drive

    U->>T: Send photo / PDF
    T->>H: Update (long polling)
    H->>H: Check allowlist (user ID)
    H->>T: Download file bytes
    H->>C: photo_to_images / pdf_to_images
    C-->>H: PNG pages (≤1600 px)
    H->>U: "⏳ Membaca struk... 0%"
    par progress ticker
        loop every 3 s (cap 90%)
            H->>U: edit "⏳ ... N%"
        end
    and extraction (to_thread)
        H->>L: system prompt + images (temperature 0)
        L-->>H: JSON
        H->>H: Pydantic validate (retry once if invalid)
    end
    H->>H: Build ExpenseRecord, dedupe_key = sha256(file)[:16]
    H->>S: find_by_dedupe_key (this + previous month)
    S-->>H: not found
    H->>D: upload_receipt → /<year>/<month>/
    D-->>H: webViewLink
    H->>S: append_expense → "Transaksi YYYY-MM"
    opt First row of a new month
        S->>S: create month tab, refresh Rekap Tahunan
    end
    H->>U: Summary + "✅ Tersimpan otomatis."
```

### Design notes

- **No confirmation step (by design).** Once a receipt is read correctly, it is saved straight away, which keeps the interaction to *one* message. If something is misread, it is fixed in the sheet itself. Fields the model couldn't read (for example a missing date) set `butuh_konfirmasi` and add a ⚠️ hint to the reply.
- **Drive first, then Sheets.** The Drive link is part of the row, so the upload must finish first. If either step fails, the user is told the receipt **was not recorded**, so nothing is ever half-saved without the user knowing.
- **Transaction date picks the tab.** A receipt from 31 August sent on 2 September goes into `Transaksi 2026-08`. If no date was found, the current WIB date is used.

## Error paths

```mermaid
flowchart TD
    A[File received] --> B{Allowed user?}
    B -- no --> X[Silently ignore]
    B -- yes --> C{PDF or photo?}
    C -- other document --> C1["Only photos or PDFs are supported"]
    C -- ok --> D[LLM extraction]
    D -- Ollama unreachable / timeout --> E1["❌ Can't reach Ollama — is the container up and the model pulled?"]
    D -- invalid JSON twice --> E2["❌ Couldn't read receipt — retake with better lighting"]
    D -- valid=false --> E3["❌ This doesn't look like a receipt (reason)"]
    D -- ok --> F{Duplicate hash?}
    F -- yes --> E4["⚠️ Already recorded"]
    F -- no --> G[Upload to Drive + append to Sheets]
    G -- exception --> E5["❌ Read OK but NOT saved — resend"]
    G -- ok --> OK["✅ Saved"]
    D -. any unexpected exception .-> GE["Global on_error: '❌ Unexpected error (Type)'"]
```

Every failure ends with a clear message in Indonesian. Nothing fails silently: a global `on_error` handler catches anything the pipeline didn't expect.

## Startup sequence

1. Load `.env` into typed `Settings`.
2. Load and refresh Google OAuth credentials, **retried 4× with exponential backoff (2 → 4 → 8 s)**. After a Windows or Docker restart, containers often start before DNS is ready. A missing credentials file is *not* retried, because waiting won't fix it.
3. Connect to Sheets and run `ensure_structure()`: set the locale, migrate the old layout, create the current month tab, the yearly recap and the dashboard. Also retried.
4. Connect to Drive (retried).
5. Ping Ollama. If it is down, the bot **logs a warning and keeps running**: recap commands still work, and extraction recovers once Ollama is up.
6. Register handlers and start polling.

## `/rekap` command

| Command | Data source | Window |
|---|---|---|
| `/rekap` or `/rekap harian` | current + previous month tabs | today |
| `/rekap mingguan` | current + previous month tabs | Monday → today (correct across month boundaries) |
| `/rekap bulanan` | current month tab | 1st → today |
| `/rekap bulanan agustus` / `2026-08` | that month's tab | whole month |
| `/rekap tahunan [2026]` | `Rekap Tahunan` tab (already aggregated by Sheets) | Jan → Dec |

A bare month name that would fall in the future is read as last year's month (asking for `januari` in March means *this* January).
