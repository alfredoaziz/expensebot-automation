"""Converts incoming Telegram files (photo bytes or PDF bytes) into a list
of PNG image bytes ready to send to the vision LLM."""

from __future__ import annotations

import io

import fitz  # PyMuPDF
from PIL import Image

_MAX_DIMENSION = 1600
_PDF_RENDER_ZOOM = 2.0


def _resize_if_needed(image: Image.Image) -> Image.Image:
    if max(image.size) <= _MAX_DIMENSION:
        return image
    ratio = _MAX_DIMENSION / max(image.size)
    new_size = (int(image.width * ratio), int(image.height * ratio))
    return image.resize(new_size, Image.LANCZOS)


def photo_to_images(photo_bytes: bytes) -> list[bytes]:
    with Image.open(io.BytesIO(photo_bytes)) as image:
        image = image.convert("RGB")
        image = _resize_if_needed(image)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return [buffer.getvalue()]


def pdf_to_images(pdf_bytes: bytes) -> list[bytes]:
    images: list[bytes] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
        matrix = fitz.Matrix(_PDF_RENDER_ZOOM, _PDF_RENDER_ZOOM)
        for page in document:
            pixmap = page.get_pixmap(matrix=matrix)
            with Image.open(io.BytesIO(pixmap.tobytes("png"))) as image:
                image = _resize_if_needed(image.convert("RGB"))
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                images.append(buffer.getvalue())
    return images
