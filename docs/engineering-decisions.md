# Engineering decisions

## Key trade-offs

| Decision | Alternatives considered | Why this one |
|---|---|---|
| **Local LLM (Ollama)** | OpenAI / Gemini Vision APIs | Financial documents stay on own hardware; zero marginal cost; works offline from AI vendors. Cost: slower, and less accurate on hard receipts. The OpenAI-compatible client keeps the option to switch. |
| **Telegram long polling** | Webhook, WhatsApp, custom web app | No public IP, TLS certificate or open port; free; works behind NAT and corporate networks |
| **Google Sheets as the database** | SQLite/Postgres + BI tool | The user already lives in Sheets; free charts; easy manual correction; nothing extra to host |
| **One tab per month** | One ever-growing tab | Keeps `get_all_records()` and dedupe scans bounded; months are easy to browse; the yearly recap is a cheap roll-up |
| **Auto-save, no confirmation** | Inline "✅ Save / ✏️ Edit" keyboard | One message per receipt keeps friction minimal; errors are rare and quick to fix in the sheet |
| **Coerce bad categories, don't reject** | Strict validation failure | A row in `Lain-lain` is better than a lost receipt |
| **Content-hash dedupe** | Telegram `file_unique_id` | Also catches the same PDF forwarded again or re-uploaded |
| **OAuth user credentials** | Service account | Files belong to the user's own Drive quota and appear in "My Drive"; no sharing setup needed |

## Reliability

- **Startup retry with exponential backoff** for every Google call (DNS often isn't ready right after boot). Configuration errors fail fast instead.
- **The bot starts even if Ollama is down**; extraction recovers once Ollama is up.
- **Self-healing spreadsheet**: `ensure_structure()` is idempotent and rebuilds a broken or outdated dashboard.
- **Best-effort side features** (charts, saving the refreshed token) log warnings and never crash the bot.
- **No silent failures**: every error path sends a specific message, plus a global `on_error` handler as a safety net.
- **Timezone-correct**: everything uses WIB (`Asia/Jakarta`) explicitly; containers run in UTC.

## Security model

| Threat | Mitigation |
|---|---|
| Strangers using the bot | Numeric Telegram user-ID allowlist; other users are silently ignored |
| Formula injection from OCR text (`=HYPERLINK(...)`, `=IMPORTXML(...)`) | `escape_formula_injection()` on every free-text field |
| Bot token leaking into logs (`httpx` logs full URLs, which contain the token) | `httpx` logger raised to `WARNING` |
| Secrets in git | `.env` and `credentials/*.json` are git-ignored; `.env.example` holds placeholders only |
| Container compromise | Non-root user, `no-new-privileges`, credentials mounted read-only, no published ports |
| LLM endpoint exposure | Ollama reachable only on the internal Docker network |

## Testing strategy

**73 unit tests in 11 modules**, all running without network access:

- **Pure logic** (`recap`, `formatting`, `schema`, `models`, `converter`): direct assertions, including edge cases such as weeks that cross a month boundary, blank `Tipe` on old rows, and Rupiah formatting of negative balances.
- **LLM client**: valid JSON, code-fence stripping, retry-then-succeed, retry-then-fail, connection error → `LLMUnavailableError`.
- **Sheets repository** (16 tests with a mocked gspread): month tab creation, rollover to a new month without a restart, dedupe window, migration of the old layout, dashboard rebuild clearing old cells, yearly recap resizing.
- **Startup retry**: success, retry-then-success, giving up, and *no retry* on missing credentials.
- **Command parsing**: `yyyy-mm`, Indonesian month names (case-insensitive), a future month resolving to the previous year.

Tooling: `pytest`, `pytest-asyncio`, `pytest-mock`, `respx`, `pytest-cov`; lint with `ruff` (`E, F, I, UP, B`).

## Development workflow

The project was built iteratively with **Claude Code** as an AI pair-programmer:

1. **Specify**: I described the goal, the constraints (local LLM, Google Sheets, Indonesian receipts, Docker) and the category taxonomy.
2. **Scaffold**: modules, config, Docker setup and tests were generated together, with clear module boundaries from the start.
3. **Test with real receipts**: every feature was run against real receipts and bank slips.
4. **Fix from real failures**: misreads and runtime problems led to targeted changes plus a regression test. Examples from the history:
   - amounts cut by ×1000 → the Rupiah normalization rule with worked examples
   - transfer slips logged in the wrong direction → the `ACCOUNT_HOLDER_NAME` rule
   - `#ERROR!` dashboards → locale pinned to `en_US`
   - month selector broken by Sheets auto-parsing dates → raw writes
   - crash on boot before DNS was ready → startup retry with backoff
   - one huge tab → per-month tabs plus automatic migration
5. **Review**: I read, ran and tested each change before keeping it; comments in the code record *why* each non-obvious choice was made.

## Known limitations

- One Telegram message = one transaction.
- The duplicate check covers the current and previous month only.
- The dashboard's "this week" figure reads the current month tab only (the `/rekap mingguan` command is correct across months).
- No confirmation step: misreads are corrected manually in the sheet.
- Accuracy depends on the model; a 4B model struggles with handwriting.

## Roadmap

- [ ] Always-on hosting (VPS or home server) independent of the laptop
- [ ] Optional inline confirm/edit before saving
- [ ] Full-history dedupe (e.g. an index tab)
- [ ] Multiple receipts per PDF
- [ ] Labelled benchmark: field-level accuracy per model (gemma3, qwen2.5-vl, llava…)
- [ ] Category budgets with over-budget alerts and a monthly summary push
- [ ] CI (GitHub Actions: ruff + pytest on push)
