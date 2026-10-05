# Bot Pencatatan Pengeluaran (Telegram + LLM Lokal + Google Sheets/Drive)

Bot Telegram yang membaca foto struk / scan PDF menggunakan LLM vision lokal
(Ollama), lalu otomatis mencatat pengeluaran ke Google Sheets (dengan
dashboard harian/mingguan/bulanan) dan menyimpan file aslinya ke Google
Drive.

## Alur

1. Kirim foto struk atau file PDF ke bot di Telegram.
2. Bot mengirim gambar ke Ollama (server lokal, OpenAI-compatible) untuk
   diekstrak menjadi data terstruktur (tanggal, merchant, kategori, item, total),
   dengan indikator progres ("⏳ Membaca struk... 40%") selama menunggu.
3. Tidak ada langkah konfirmasi manual — begitu berhasil dibaca, bot langsung
   mengunggah file asli ke Google Drive dan menambahkan baris baru ke tab
   **Transaksi &lt;tahun&gt;-&lt;bulan&gt;** (mis. `Transaksi 2026-09`) sesuai
   tanggal transaksi di Google Sheets, lalu membalas dengan ringkasan + "✅
   Tersimpan otomatis." Struk yang identik (hash file sama) tidak dicatat
   dua kali. Tab bulan baru dibuat otomatis saat transaksi pertama bulan itu
   masuk — bot tidak perlu di-restart saat pergantian bulan.
4. Tab **Dashboard** punya sel pilih bulan (B1, format `yyyy-mm`, default
   bulan berjalan) — ganti isinya untuk lihat ringkasan + chart kategori
   bulan lain, termasuk bulan-bulan lama. Tab **Rekap Tahunan** merangkum
   Pemasukan/Pengeluaran/Saldo per bulan dari semua tab `Transaksi
   <yyyy-mm>` yang ada (plus chart tren Pemasukan vs Pengeluaran per
   bulan), jadi riwayat bulan-bulan sebelumnya tetap bisa ditrack di satu
   tempat.
5. `/rekap harian|mingguan|bulanan` membalas ringkasan bulan berjalan
   langsung di Telegram. Tambahkan nama bulan untuk lihat bulan lain, mis.
   `/rekap bulanan agustus` atau `/rekap bulanan 2026-08`. `/rekap
   tahunan [tahun]` membalas rekap per bulan + total setahun (default
   tahun berjalan).

## Setup

### 1. Buat bot Telegram

Chat dengan [@BotFather](https://t.me/BotFather) → `/newbot` → salin token
ke `TELEGRAM_BOT_TOKEN`. Cari user ID Telegram-mu via
[@userinfobot](https://t.me/userinfobot) → isi `TELEGRAM_ALLOWED_USER_IDS`
(hanya user ID ini yang boleh memakai bot).

### 2. Setup Google Cloud (Sheets + Drive)

1. Buka [Google Cloud Console](https://console.cloud.google.com/), buat
   project baru (atau pakai yang sudah ada).
2. Aktifkan **Google Sheets API** dan **Google Drive API**.
3. Buka **APIs & Services → Credentials → Create Credentials → OAuth client
   ID**, pilih tipe **Desktop app**.
4. Unduh file JSON-nya, simpan sebagai `credentials/client_secret.json`.

### 3. Otorisasi akun Google (sekali, di host — bukan di Docker)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
python scripts/authorize_google.py
```

Browser akan terbuka untuk login. Setelah selesai, `credentials/authorized_user.json`
akan otomatis dibuat — file inilah yang dipakai bot di dalam Docker.

### 4. Siapkan Spreadsheet & folder Drive

1. Buat Google Spreadsheet baru (kosong), salin **ID-nya saja** dari URL
   (`https://docs.google.com/spreadsheets/d/<ID_INI>/edit` → bagian `<ID_INI>`
   saja, tanpa `https://...` atau `/edit`) ke `GOOGLE_SPREADSHEET_ID`.
2. Buat folder Google Drive untuk menyimpan struk, salin **ID folder-nya
   saja** (dari `https://drive.google.com/drive/folders/<ID_INI>`) ke
   `GOOGLE_DRIVE_FOLDER_ID`.

### 5. Konfigurasi `.env`

```bash
cp .env.example .env
```

Isi semua nilai sesuai langkah 1–4 di atas. `LLM_BASE_URL`/`LLM_API_KEY`
biarkan default (`http://ollama:11434/v1` / `ollama`) — Ollama jalan sebagai
service Docker sendiri, bot terhubung ke situ lewat network internal Docker,
bukan lewat host.

### 6. Jalankan semuanya (bot + Ollama)

```bash
docker compose up -d --build
```

Lalu **pull model vision** ke dalam container Ollama (sekali saja, model
tersimpan di volume Docker jadi tidak perlu diulang setelah restart):

```bash
docker exec -it ollama ollama pull gemma3:4b
```

Ganti `gemma3:4b` kalau mau pakai model vision lain — pastikan nama tag-nya
sama persis dengan `LLM_MODEL` di `.env`. Cek daftar model yang tersedia di
[ollama.com/library](https://ollama.com/library) (cari yang mendukung
image input / vision).

```bash
docker compose logs -f expensebot
```

Saat startup, bot otomatis memastikan tab bulan berjalan
(**Transaksi &lt;yyyy-mm&gt;**), **Rekap Tahunan**, dan **Dashboard** ada di
spreadsheet (`ensure_structure()` di `google/sheets.py`). Kalau spreadsheet
masih pakai tab **Transaksi** gaya lama (sebelum fitur ini ada), isinya
otomatis dipecah ke tab bulanan berdasarkan tanggal transaksi, lalu tab lama
itu dihapus — migrasi sekali jalan, tidak akan terjadi lagi setelahnya.

### Jalan 24 jam

- Ollama & bot sama-sama `restart: unless-stopped` — otomatis nyala lagi
  kalau Docker Desktop / Windows restart, selama Docker Desktop diset untuk
  **start saat login** (Docker Desktop → Settings → General → "Start Docker
  Desktop when you sign in to your computer").
- Windows sendiri jangan sampai sleep: **Settings → System → Power & sleep
  → Sleep = Never**.
- Punya GPU NVIDIA? Buka `docker-compose.yml`, uncomment blok `deploy` di
  service `ollama` supaya inference jauh lebih cepat daripada CPU-only.

## Development

```bash
pip install -r requirements-dev.txt
pytest --cov=src --cov-report=term-missing
ruff check src tests
```

## Struktur Proyek

```
src/expensebot/
  config.py            Pengaturan dari .env
  main.py               Entry point bot (polling)
  models.py              ExpenseRecord, ExpenseItem
  telegram/handlers.py    /start, /rekap, alur foto/PDF -> konfirmasi
  telegram/formatting.py  Format pesan Bahasa Indonesia
  llm/client.py           Klien Ollama (OpenAI-compatible)
  llm/prompts.py          Prompt ekstraksi struk
  llm/schema.py           Validasi output LLM
  documents/converter.py  Foto/PDF -> gambar PNG
  google/auth.py          OAuth credentials
  google/sheets.py         Tab Transaksi <yyyy-mm> + Rekap Tahunan + Dashboard
  google/drive.py          Upload struk ke Drive
  recap.py                 Agregasi harian/mingguan/bulanan
  timeutil.py              Helper waktu WIB (Asia/Jakarta)
scripts/
  authorize_google.py     Login Google sekali (di host)
  init_google_sheet.py     (Re)buat struktur sheet secara manual
```

## Keamanan

- Hanya user ID di `TELEGRAM_ALLOWED_USER_IDS` yang bisa memakai bot.
- `.env` dan `credentials/*.json` tidak pernah masuk git (lihat `.gitignore`).
- Semua teks dari OCR/LLM yang ditulis ke Sheets di-escape agar tidak bisa
  menjalankan formula (`google/sheets.py::escape_formula_injection`).
- Bot berjalan sebagai user non-root di dalam container dan tidak membuka
  port masuk (mode polling ke Telegram, bukan webhook). Ollama juga tidak
  membuka port ke host secara default — hanya bisa diakses bot lewat
  network internal Docker.

## Keterbatasan V1

- Satu pesan Telegram = satu transaksi (PDF multi-halaman dianggap satu
  struk panjang, bukan banyak struk terpisah).
- Tidak ada langkah konfirmasi manual — struk yang berhasil dibaca langsung
  tersimpan. Kalau LLM salah baca (kategori/nominal keliru), edit langsung
  di Google Sheets; struk yang gagal dibaca sama sekali tidak akan
  tersimpan (bot membalas pesan error, bukan mencatat data kosong/salah).
- Cek struk duplikat (hash file sama) hanya menjangkau tab bulan berjalan +
  bulan sebelumnya, bukan seluruh riwayat — struk yang dikirim ulang lebih
  dari sebulan setelah aslinya tidak akan terdeteksi sebagai duplikat.
- Widget "Pengeluaran Minggu Ini" di tab Dashboard hanya membaca data dari
  tab bulan berjalan; kalau awal minggu jatuh di bulan sebelumnya, hari-hari
  itu tidak ikut terhitung di angka tersebut. `/rekap mingguan` di Telegram
  tetap akurat karena membaca lintas tab bulanan.
