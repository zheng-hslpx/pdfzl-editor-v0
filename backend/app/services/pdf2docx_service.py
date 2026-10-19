"""PDF -> Word (docx) conversion via pdf2docx.

pdf2docx is a rule-based layout reconstruction engine: output is
best-effort. Scanned PDFs, complex multi-column layouts and artistic
typography will not round-trip faithfully — surfaced to users as a
notice in the UI and documented in the README.
"""
from __future__ import annotations

from pathlib import Path

from ..core.errors import PdfError
from ..core.tempfile import temp_manager

DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def convert_pdf_to_docx(src: Path) -> Path:
    dest = temp_manager.create_temp_file(".docx")
    try:
        from pdf2docx import Converter

        cv = Converter(str(src))
        try:
            cv.convert(str(dest))
        finally:
            cv.close()
    except PdfError:
        raise
    except Exception as exc:
        temp_manager.discard(dest)
        raise PdfError(500, f"PDF 转 Word 失败: {exc}") from exc
    if not dest.exists() or dest.stat().st_size == 0:
        temp_manager.discard(dest)
        raise PdfError(500, "PDF 转 Word 失败：输出为空")
    return dest
