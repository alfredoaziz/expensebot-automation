"""Parser tests on synthetic emails that mirror the real BSI BYOND / blu
templates' HTML structure (all names, numbers and amounts are made up)."""

import datetime as dt

import pytest

from expensebot.gmail.html_text import html_to_lines, plain_text_to_lines
from expensebot.gmail.message import EmailMessage
from expensebot.gmail.parsers import find_parser, parse_rupiah, parse_tanggal_waktu
from expensebot.timeutil import WIB

HOLDER = "Budi Santoso"
BSI = "nonereply.byondbybsi@bankbsi.co.id"
BLU = "receipts@blubybcadigital.id"


def _bsi_html(judul: str, rows: list[tuple[str, str]], extra: str = "") -> str:
    cells = "".join(
        f'<tr><td class="content-label">{label}</td><td class="content-value">{value}</td></tr>'
        for label, value in rows
    )
    return (
        "<html><head><style>.x{color:red}</style></head><body><table>"
        f'<tr><td><p style="font-weight:bold">\r\n  {judul}</p></td></tr>'
        "<tr><td><p>Assalamu'alaikum BUDI SANTOSO,</p>"
        "<p>Berikut merupakan detail transaksi anda :</p></td></tr>"
        f"{extra}<tr><td><table>{cells}</table></td></tr>"
        "<tr><td><p>Rekening Sumber</p><p>BUDI SANTOSO</p><p>******1234</p></td></tr>"
        "</table></body></html>"
    )


def _msg(sender: str, subject: str, html: str) -> EmailMessage:
    return EmailMessage(
        message_id="abc123",
        sender=sender,
        subject=subject,
        received_at=dt.datetime(2026, 10, 9, 20, 0, tzinfo=WIB),
        lines=tuple(html_to_lines(html)),
    )


def _parse(sender: str, subject: str, html: str, holder: str = HOLDER):
    parser = find_parser(sender)
    assert parser is not None
    return parser(_msg(sender, subject, html), holder)


# --- helpers -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Rp 720.000,00", 720000),
        ("Rp183.110,71", 183110.71),
        ("Rp 24.000.000", 24000000),
        ("Rp600.000,00", 600000),
        ("Rp 0", 0),
        ("IDR 1.500", 1500),
        ("bukan angka", None),
        (None, None),
    ],
)
def test_parse_rupiah_format_indonesia(text, expected):
    assert parse_rupiah(text) == expected


def test_parse_tanggal_waktu_bulan_indonesia_dan_jam():
    assert parse_tanggal_waktu("05 Agu 2026 11:09:55 WIB") == (
        dt.date(2026, 8, 5),
        "11:09",
    )
    assert parse_tanggal_waktu("09 Okt 2026") == (dt.date(2026, 10, 9), None)
    assert parse_tanggal_waktu("19:47:28 WIB") == (None, "19:47")
    assert parse_tanggal_waktu(None) == (None, None)


def test_html_to_lines_menggabung_span_inline_dan_buang_style():
    html = (
        "<style>p{}</style><div>Nominal Transfer</div>"
        "<div><span>Rp</span><span><span>600.000</span></span><span>,00</span></div>"
    )
    assert html_to_lines(html) == ["Nominal Transfer", "Rp600.000,00"]


def test_plain_text_to_lines_memecah_sel_tabel():
    assert plain_text_to_lines("| Tanggal | 09 Okt 2026 |\n\nJam\t19:47 WIB") == [
        "Tanggal",
        "09 Okt 2026",
        "Jam",
        "19:47 WIB",
    ]


def test_message_from_api_decode_html_multipart_dan_header():
    import base64

    from expensebot.gmail.client import message_from_api

    def b64(text: str) -> str:
        return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")

    raw = {
        "id": "xyz",
        "internalDate": "1791550049000",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": "BSI <NoneReply.ByondByBSI@bankbsi.co.id>"},
                {"name": "Subject", "value": "Transfer Berhasil"},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": b64("plain")}},
                {
                    "mimeType": "text/html",
                    "body": {"data": b64("<p>Nominal</p><p>Rp 1</p>")},
                },
            ],
        },
    }

    msg = message_from_api(raw)

    assert msg.sender == "nonereply.byondbybsi@bankbsi.co.id"
    assert msg.subject == "Transfer Berhasil"
    assert msg.lines == ("Nominal", "Rp 1")
    assert msg.received_at.tzinfo is not None
    assert msg.gmail_link.endswith("/xyz")


def test_find_parser_hanya_untuk_bank_yang_dikenal():
    assert find_parser(BSI) is not None
    assert find_parser(BLU) is not None
    assert find_parser("info@jago.com") is None


# --- BSI BYOND -----------------------------------------------------------------


def test_bsi_qris_jadi_pengeluaran_dengan_merchant():
    html = _bsi_html(
        "Transaksi pembayaran QRIS MPM kamu berhasil",
        [
            ("Nominal Transaksi", "Rp 720.000,00"),
            ("Tanggal", "09 Okt 2026"),
            ("Jam", "19:47:28 WIB"),
            ("Nama Merchant", "KLINIK GIGI SEHAT"),
            ("Total", "Rp 720.000,00"),
        ],
    )

    parsed = _parse(BSI, "Transaksi Pembayaran QRIS MPM Kamu Berhasil", html)

    assert parsed.bank == "BSI"
    assert parsed.tipe == "Pengeluaran"
    assert parsed.jenis == "QRIS"
    assert parsed.counterparty == "KLINIK GIGI SEHAT"
    assert parsed.total == 720000
    assert parsed.tanggal == dt.date(2026, 10, 9)
    assert parsed.waktu == "19:47"
    assert parsed.kategori_hint is None
    assert parsed.is_self_transfer is False


def test_bsi_topup_ewallet_langsung_e_commerce():
    html = _bsi_html(
        "Pembelian Saldo E-Wallet Berhasil",
        [
            ("Nominal Top Up", "Rp 50.000"),
            ("Tanggal", "09 Okt 2026"),
            ("Jam", "19:46:43 WIB"),
            ("Pembelian", "ShopeePay"),
            ("Biaya Admin", "Rp 1.000"),
            ("Total", "Rp 51.000"),
        ],
    )

    parsed = _parse(BSI, "Pembelian Saldo E-Wallet Berhasil", html)

    assert parsed.counterparty == "Top Up ShopeePay"
    assert parsed.kategori_hint == "E-commerce"
    assert parsed.total == 51000


def test_bsi_pembayaran_shopee_e_commerce():
    html = _bsi_html(
        "Pembayaran Shopee Berhasil",
        [
            ("Nominal Pembayaran", "Rp 156.480"),
            ("Pembayaran", "Shopee"),
            ("Total", "Rp 156.480"),
        ],
    )

    parsed = _parse(BSI, "Pembayaran Shopee Berhasil", html)

    assert parsed.counterparty == "Shopee"
    assert parsed.kategori_hint == "E-commerce"
    assert parsed.total == 156480


def test_bsi_transfer_ke_orang_lain_total_termasuk_biaya():
    penerima = "<tr><td><p>Rekening Penerima</p><p>SITI AMINAH</p><p>Bank X - 999</p></td></tr>"
    html = _bsi_html(
        "Transfer Berhasil",
        [
            ("Nominal Transfer", "Rp 24.000.000"),
            ("Biaya Transfer", "Rp 2.500"),
            ("Tanggal", "03 Okt 2026"),
            ("Jam", "11:15:06 WIB"),
            ("Keterangan", "cicilan motor"),
        ],
        extra=penerima,
    )

    parsed = _parse(BSI, "Notifikasi Transaksi Transfer", html)

    assert parsed.jenis == "Transfer"
    assert parsed.counterparty == "SITI AMINAH"
    assert parsed.total == 24002500
    assert parsed.keterangan == "cicilan motor"
    assert parsed.is_self_transfer is False
    assert "Biaya" in (parsed.catatan or "")


def test_bsi_transfer_ke_rekening_sendiri_ditandai():
    penerima = (
        "<tr><td><p>Rekening Penerima</p><p>BUDI SANTOSO</p><p>Blu - 111</p></td></tr>"
    )
    html = _bsi_html(
        "Transfer Berhasil", [("Nominal Transfer", "Rp 600.000")], extra=penerima
    )

    parsed = _parse(BSI, "Notifikasi Transaksi Transfer", html)

    assert parsed.is_self_transfer is True


def test_bsi_email_promo_tanpa_nominal_bukan_transaksi():
    html = (
        "<html><body><p>Promo spesial akhir tahun!</p><p>Diskon 50%</p></body></html>"
    )

    assert _parse(BSI, "Promo BYOND", html) is None


# --- blu by BCA Digital --------------------------------------------------------


def _blu_html(
    intro: str, amount_label: str, amount: tuple[str, str], parties: str, tail: str
):
    rupiah, sen = amount
    return (
        f"<div>Hai <strong>BUDI</strong>,<br>{intro}</div>"
        f"<div>{amount_label}</div>"
        f"<div><span>Rp</span><span><span>{rupiah}</span></span><span>{sen}</span></div>"
        f"<table><tr>{parties}</tr></table>{tail}"
    )


def _blu_party(name: str, detail: str) -> str:
    return (
        f"<td><div><img src='x'></div><div><span>{name}</span></div>"
        f"<div><span>{detail}</span></div></td>"
    )


def test_blu_transfer_masuk_dari_orang_lain_jadi_pemasukan():
    html = _blu_html(
        "Ada transaksi masuk ke rekening bluAccount kamu. Ini detailnya:",
        "Nominal Transfer",
        ("1.250.000", ",00"),
        _blu_party("ANDI WIJAYA", "BANK MANDIRI")
        + "<td><img src='arrow'></td>"
        + _blu_party("BUDI SANTOSO", "bluAccount<br/>0000 1111 2222"),
        "<div>Tgl &amp; Jam Transaksi</div><div><span>05 Okt 2026 11:08:04 WIB</span></div>"
        "<div>Tipe Transaksi</div><div><span>BI-FAST</span></div>",
    )

    parsed = _parse(BLU, "Info Transaksi Masuk ke blu Kamu 💸", html)

    assert parsed.bank == "blu"
    assert parsed.tipe == "Pemasukan"
    assert parsed.counterparty == "ANDI WIJAYA"
    assert parsed.total == 1250000
    assert parsed.tanggal == dt.date(2026, 10, 5)
    assert parsed.waktu == "11:08"
    assert parsed.kategori_hint == "Transfer Masuk"
    assert parsed.is_self_transfer is False
    assert "BANK MANDIRI" in (parsed.catatan or "")


def test_blu_transfer_masuk_dari_rekening_sendiri_ditandai_tanpa_spasi_nama():
    html = _blu_html(
        "Ada transaksi masuk ke rekening bluAccount kamu.",
        "Nominal Transfer",
        ("600.000", ",00"),
        _blu_party("BUDISANTOSO", "BANK SYARIAH INDONESIA")
        + _blu_party("BUDI SANTOSO", "bluAccount<br/>0000 1111 2222"),
        "<div>Tgl &amp; Jam Transaksi</div><div>05 Okt 2026 11:08:04 WIB</div>",
    )

    parsed = _parse(BLU, "Info Transaksi Masuk ke blu Kamu", html)

    assert parsed.is_self_transfer is True


def test_blu_pembayaran_kartu_debit_jadi_pengeluaran_dengan_desimal():
    html = _blu_html(
        "Terima kasih sudah menggunakan blu untuk transaksimu.",
        "Total Bayar",
        ("183.110", ",71"),
        _blu_party("BUDI SANTOSO", "bluAccount")
        + _blu_party("Upwork -123Membe", "bluDebit Card •••• 7245"),
        "<div>Nominal dalam USD</div><div>USD9,99</div>"
        "<div>Tgl &amp; Jam Transaksi</div><div>05 Okt 2026 11:09:55 WIB</div>"
        "<div>Tipe Transaksi</div><div>Debit Online (Recurring/Autopay)</div>",
    )

    parsed = _parse(BLU, "Transaksimu Pakai blu Berhasil", html)

    assert parsed.tipe == "Pengeluaran"
    assert parsed.counterparty == "Upwork -123Membe"
    assert parsed.total == 183110.71
    assert parsed.jenis == "Debit Online (Recurring/Autopay)"
    assert "USD9,99" in (parsed.catatan or "")
    assert parsed.kategori_hint is None


def test_blu_email_tanpa_nominal_bukan_transaksi():
    assert _parse(BLU, "Info", "<div>Yuk update aplikasi blu kamu</div>") is None
