import io

import fitz
from PIL import Image

from expensebot.documents.converter import pdf_to_images, photo_to_images


def _make_png_bytes(width: int = 100, height: int = 100) -> bytes:
    image = Image.new("RGB", (width, height), color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _make_pdf_bytes(pages: int = 1) -> bytes:
    document = fitz.open()
    for _ in range(pages):
        page = document.new_page()
        page.insert_text((72, 72), "Struk contoh")
    pdf_bytes = document.tobytes()
    document.close()
    return pdf_bytes


def test_photo_to_images_menghasilkan_satu_gambar_png():
    # Arrange
    photo_bytes = _make_png_bytes()

    # Act
    images = photo_to_images(photo_bytes)

    # Assert
    assert len(images) == 1
    assert images[0].startswith(b"\x89PNG")


def test_photo_to_images_mengecilkan_gambar_besar():
    # Arrange
    photo_bytes = _make_png_bytes(width=3000, height=2000)

    # Act
    images = photo_to_images(photo_bytes)

    # Assert
    with Image.open(io.BytesIO(images[0])) as resized:
        assert max(resized.size) <= 1600


def test_pdf_to_images_satu_gambar_per_halaman():
    # Arrange
    pdf_bytes = _make_pdf_bytes(pages=2)

    # Act
    images = pdf_to_images(pdf_bytes)

    # Assert
    assert len(images) == 2
    for image_bytes in images:
        assert image_bytes.startswith(b"\x89PNG")
