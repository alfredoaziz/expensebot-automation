# Setup & deployment

## Prerequisites

- Docker Desktop (Windows/macOS) or Docker Engine + Compose (Linux)
- Python 3.11+ on the host (only for the one-time Google login)
- A Telegram account and a Google account
- Optional: an NVIDIA GPU for much faster inference

## 1. Telegram bot

1. Message [@BotFather](https://t.me/BotFather), send `/newbot`, and copy the token into `TELEGRAM_BOT_TOKEN`.
2. Get your numeric user ID from [@userinfobot](https://t.me/userinfobot) and put it in `TELEGRAM_ALLOWED_USER_IDS` (comma-separated for several users). **Updates from anyone else are ignored.**

## 2. Google Cloud (Sheets + Drive)

1. In [Google Cloud Console](https://console.cloud.google.com/), create or select a project.
2. Enable the **Google Sheets API** and the **Google Drive API**.
3. Go to *APIs & Services → Credentials → Create credentials → OAuth client ID → Desktop app*.
4. Download the JSON and save it as `credentials/client_secret.json`.

## 3. One-time authorization (on the host)

```bash
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
python scripts/authorize_google.py
```

This opens a browser for login and writes `credentials/authorized_user.json` (contains a refresh token). The container mounts `credentials/` **read-only**.

## 4. Spreadsheet and Drive folder

- Create an empty Google Spreadsheet and copy **only the ID** from `https://docs.google.com/spreadsheets/d/<ID>/edit` into `GOOGLE_SPREADSHEET_ID`.
- Create a Drive folder and copy its ID from `https://drive.google.com/drive/folders/<ID>` into `GOOGLE_DRIVE_FOLDER_ID`.

## 5. Configuration reference (`.env`)

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | – | Token from BotFather |
| `TELEGRAM_ALLOWED_USER_IDS` | ✅ | – | Comma-separated allowlist |
| `LLM_MODEL` | ✅ | – | Ollama tag, e.g. `gemma3:4b` (must match the pulled model exactly) |
| `LLM_BASE_URL` | | `http://ollama:11434/v1` | Any OpenAI-compatible endpoint |
| `LLM_API_KEY` | | `ollama` | Ignored by Ollama; needed for hosted providers |
| `ACCOUNT_HOLDER_NAME` | | *(empty)* | Your name as shown on bank/e-wallet slips; used to tell income from expense on transfers |
| `GOOGLE_SPREADSHEET_ID` | ✅ | – | Spreadsheet ID only |
| `GOOGLE_DRIVE_FOLDER_ID` | ✅ | – | Folder ID only |
| `GOOGLE_CREDENTIALS_DIR` | | `/app/credentials` | Path inside the container |
| `TIMEZONE` | | `Asia/Jakarta` | |
| `LOG_LEVEL` | | `INFO` | |

## 6. Run

```bash
docker compose up -d --build
docker exec -it ollama ollama pull gemma3:4b   # once — stored in the ollama_data volume
docker compose logs -f expensebot
```

Expected log: `Bot dimulai (polling)...`. Send `/start` to your bot.

## GPU vs CPU

`docker-compose.yml` reserves an NVIDIA GPU for the `ollama` service (Docker Desktop's WSL2 backend passes it through on Windows). **No GPU?** Remove the `deploy:` block to run on CPU only. It works, but each receipt takes longer to read.

## Running 24/7

Both services use `restart: unless-stopped`. On a Windows host:

- Docker Desktop → Settings → General → **Start Docker Desktop when you sign in**
- Windows → Power & sleep → **Sleep: Never**

The bot only makes outbound connections and holds no state besides the read-only credentials, so the same `docker compose up` works unchanged on a home server, mini-PC or VPS. On a VPS without a GPU, pick a smaller model or point `LLM_BASE_URL` at a hosted OpenAI-compatible endpoint.

## Networks with TLS inspection (corporate proxy)

Some office networks re-sign HTTPS traffic with their own CA, which breaks calls to Telegram and Google from inside the container. The `Dockerfile` adds a CA bundle from `docker/certs/bps-ca-bundle.pem` (not included in this repo — certificates are git-ignored) to **both** the system trust store and `certifi`'s bundle (`httpx` and `requests` use certifi, not the system store).

> **If you are not behind such a proxy**, remove the two `COPY docker/certs/...` / `update-ca-certificates` lines and the `cat ... >> certifi` step from the `Dockerfile`, or replace the file with your own network's CA.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `❌ Tidak bisa menghubungi Ollama` | Ollama container down, or the model isn't pulled or doesn't match `LLM_MODEL` |
| `authorized_user.json tidak ditemukan` | Step 3 not done, or `credentials/` not mounted |
| Startup fails on Sheets | Full URL pasted instead of the ID, or the Google account has no access to the sheet |
| Dashboard shows `#ERROR!` | Locale changed by hand; restart the bot (it sets `en_US` again) |
| Bot is slow | CPU-only inference; enable the GPU or use a smaller model |
| SSL errors in logs | Behind a TLS-inspecting proxy; see the section above |

## Development

```bash
pip install -r requirements-dev.txt
pytest --cov=src --cov-report=term-missing
ruff check src tests
```
