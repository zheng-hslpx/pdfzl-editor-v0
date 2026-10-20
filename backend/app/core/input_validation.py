"""Content-based checks for uploaded raster images."""
from __future__ import annotations

from pathlib import Path

import fitz
from PIL import Image, UnidentifiedImageError

from .errors import PdfError

ALLOWED_IMAGE_FORMATS = {"PNG", "JPEG", "BMP", "WEBP", "TIFF", "GIF"}


def validate_pdf(path: Path) -> None:
    """Parse the upload and reject non-PDF, empty, or encrypted documents."""
    try:
        document = fitz.open(str(path))
    except Exception as exc:  # noqa: BLE001 - PyMuPDF raises several parser errors
        raise PdfError(400, "上传内容不是可读取的 PDF", code="INVALID_PDF") from exc
    try:
        if document.is_encrypted and not document.authenticate(""):
            raise PdfError(400, "PDF 已加密，暂不支持处理加密文件", code="ENCRYPTED_PDF")
        if document.page_count == 0:
            raise PdfError(400, "PDF 没有任何页面", code="INVALID_PDF")
    finally:
        document.close()


def validate_image(path: Path) -> None:
    """Reject extension-spoofed or corrupt image uploads before PDF mutation."""
    try:
        with Image.open(path) as image:
            if image.format not in ALLOWED_IMAGE_FORMATS:
                raise PdfError(422, "不支持的图片内容格式", code="INVALID_IMAGE")
            image.verify()
    except PdfError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise PdfError(422, "上传内容不是有效图片", code="INVALID_IMAGE") from exc
