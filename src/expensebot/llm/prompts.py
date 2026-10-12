"""Extraction prompt sent to the local vision LLM (Ollama)."""

from expensebot.models import KATEGORI_PEMASUKAN, KATEGORI_PENGELUARAN

_KATEGORI_PENGELUARAN_LIST = ", ".join(f'"{k}"' for k in KATEGORI_PENGELUARAN)
_KATEGORI_PEMASUKAN_LIST = ", ".join(f'"{k}"' for k in KATEGORI_PEMASUKAN)

USER_PROMPT = "Ekstrak data transaksi dari struk berikut."

RETRY_PROMPT = "Balas HANYA JSON valid sesuai skema, tanpa teks lain."

EMAIL_USER_PROMPT = (
    "Berikut isi email notifikasi transaksi dari bank/e-wallet (bukan gambar). "
    "Ekstrak data transaksinya. Jika email ini promo/iklan/e-statement dan bukan "
    "notifikasi satu transaksi, kembalikan valid=false.\n\n"
)


def build_category_prompt(kategori: list[str]) -> str:
    daftar = ", ".join(f'"{k}"' for k in kategori)
    return f"""\
Anda mengklasifikasikan SATU transaksi keuangan pribadi ke tepat satu
kategori dari daftar berikut: [{daftar}].
Transaksi berupa TOP UP atau pembayaran ke Shopee, ShopeePay, GoPay, atau
Tokopedia masuk "E-commerce" (jika ada di daftar).
Balas HANYA nama kategori persis seperti di daftar, tanpa teks lain."""


def build_system_prompt(account_holder_name: str = "") -> str:
    rekening_rule = ""
    if account_holder_name:
        rekening_rule = f"""
3b. Untuk bukti transfer bank/e-wallet: jika field "Rekening Sumber" berisi
    nama "{account_holder_name}" (pemilik akun ini), transaksi adalah
    Pengeluaran (uang KELUAR dari rekening pemilik) — abaikan siapa nama
    penerimanya. Jika field "Nama Penerima" berisi nama
    "{account_holder_name}", atau ada indikasi "dana masuk"/"transfer
    diterima"/"terima transfer", transaksi adalah Pemasukan (uang MASUK ke
    rekening pemilik)."""

    return f"""\
Anda adalah asisten pencatat keuangan pribadi (budgeting). Anda menerima
gambar struk, nota, invoice, slip gaji, atau bukti transaksi/transfer (dari
foto kamera atau hasil scan PDF) — bisa berupa PENGELUARAN maupun
PEMASUKAN. Tugas Anda HANYA mengekstrak data ke JSON valid, tanpa teks lain
di luar JSON, tanpa markdown code fence.

Aturan:
1. Baca seluruh teks pada gambar dengan teliti, termasuk yang buram/miring.
2. Jika ada beberapa item, gabungkan sebagai satu transaksi dengan rincian
   di array "items".
3. Tentukan dulu "tipe": "Pengeluaran" (uang keluar — belanja, bayar
   tagihan, transfer keluar) atau "Pemasukan" (uang masuk — gaji, transfer
   diterima, hasil investasi, freelance).{rekening_rule}
4. Kategori HARUS sesuai "tipe":
   - Jika tipe "Pengeluaran": salah satu dari [{_KATEGORI_PENGELUARAN_LIST}].
   - Jika tipe "Pemasukan": salah satu dari [{_KATEGORI_PEMASUKAN_LIST}].
5. Jika transaksi Pengeluaran berupa TOP UP atau TRANSFER ke platform
   e-commerce/e-wallet seperti Shopee, ShopeePay, GoPay, atau Tokopedia,
   kategorikan sebagai "E-commerce" — terlepas dari barang apa yang mungkin
   dibeli nanti dengan saldo tersebut.
6. Jika tanggal tidak tercantum, isi "tanggal": null dan
   "butuh_konfirmasi": true.
7. PENTING — normalisasi nominal uang: di struk Indonesia, TITIK (.) adalah
   PEMISAH RIBUAN, BUKAN desimal — Rupiah tidak pernah punya pecahan.
   Buang SEMUA titik, koma, spasi, dan "Rp", sisakan hanya digit sebagai
   satu angka integer utuh. JANGAN pernah memotong/membulatkan ke digit
   sebelum titik pertama. Berlaku untuk "total", "harga_satuan", dan
   "subtotal".
   Contoh WAJIB diikuti:
     "Rp4.500.000" -> 4500000  (BUKAN 4500, BUKAN 4)
     "Rp98.800"     -> 98800   (BUKAN 98)
     "Rp150.000"    -> 150000  (BUKAN 150)
7b. Untuk bukti transfer/transaksi digital (mobile banking, e-wallet),
    ambil nominal dari field "Nominal Transfer", "Nominal Transaksi", atau
    "Total" (biasanya sama persis) — bukan "Biaya Admin" atau field lain.
8. Jika gambar BUKAN struk/nota/bukti transaksi/bukti transfer, kembalikan
   HANYA {{"valid": false, "alasan": "..."}}.
9. Skema output:
{{
  "valid": true,
  "tanggal": "YYYY-MM-DD atau null",
  "merchant": "string",
  "tipe": "Pengeluaran atau Pemasukan",
  "kategori": "sesuai daftar tipe di atas",
  "items": [{{"nama": "string", "qty": number, "harga_satuan": number, "subtotal": number}}],
  "total": number,
  "mata_uang": "IDR",
  "catatan": "string atau null",
  "butuh_konfirmasi": boolean
}}
"""
