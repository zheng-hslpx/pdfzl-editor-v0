"""PyMuPDF (fitz) adapter — the single PDF engine boundary.

All PDF operations in the app go through this module (mirrors how
Stirling-PDF centralizes PDF work in CustomPDFDocumentFactory +
PdfUtils). Operations are added per milestone; M1 keeps only the
primitives everything else needs.
"""
from __future__ import annotations

import os
from pathlib import Path

import fitz  # PyMuPDF

from .errors import PdfError

# Base-14 built-ins available without shipping font files.
FONT_HELV = "helv"
FONT_HEBO = "hebo"
FONT_HEIT = "heit"
FONT_CJK = "china-s"  # PyMuPDF built-in CJK font (renders + extracts Chinese)


def pick_font(text: str) -> str:
    """helv for latin, china-s when the text contains CJK codepoints."""
    return FONT_CJK if any(ord(c) > 0x2E80 for c in text) else FONT_HELV


def open_pdf(path: Path) -> fitz.Document:
    """Open a PDF, mapping failures to a 400 PdfError."""
    try:
        doc = fitz.open(str(path))
    except Exception as exc:  # noqa: BLE001 - fitz raises broad types
        raise PdfError(400, "上传内容不是可读取的 PDF", code="INVALID_PDF") from exc
    if not doc.is_pdf:
        doc.close()
        raise PdfError(400, "上传内容不是可读取的 PDF", code="INVALID_PDF")
    if doc.is_encrypted:
        if not doc.authenticate(""):
            doc.close()
            raise PdfError(400, "PDF 已加密，暂不支持处理加密文件", code="ENCRYPTED_PDF")
    if doc.page_count == 0:
        doc.close()
        raise PdfError(400, "PDF 没有任何页面", code="INVALID_PDF")
    return doc


def parse_page_ranges(spec: str, total: int) -> list[int]:
    """Parse "1,3,5-8,all" into a sorted list of 0-based page indices.

    Raises PdfError(400) on malformed or out-of-range input.
    """
    spec = (spec or "").strip()
    if not spec:
        raise PdfError(400, "页码范围不能为空")
    if spec.lower() == "all":
        return list(range(total))
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            bits = part.split("-", 1)
            try:
                start, end = int(bits[0]), int(bits[1])
            except ValueError as exc:
                raise PdfError(400, f"无效的页码范围: {part}") from exc
            if start < 1 or end > total or start > end:
                raise PdfError(
                    400, f"页码范围越界或无效: {part}（文档共 {total} 页）"
                )
            pages.update(range(start - 1, end))
        else:
            try:
                page = int(part)
            except ValueError as exc:
                raise PdfError(400, f"无效的页码: {part}") from exc
            if page < 1 or page > total:
                raise PdfError(
                    400, f"页码越界: {page}（文档共 {total} 页）"
                )
            pages.add(page - 1)
    if not pages:
        raise PdfError(400, "页码范围解析结果为空")
    return sorted(pages)


def save_atomic(doc: fitz.Document, path: Path, **options) -> None:
    """Save document to path atomically (tmp + os.replace)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    doc.save(str(tmp), **options)
    os.replace(tmp, path)


def commit_and_reload(doc: fitz.Document, path: Path, **options) -> fitz.Document:
    """Atomically save doc back over `path` and return a fresh handle.

    Required when saving over the document's own source file: PyMuPDF
    holds the source open, and os.replace over an open handle fails
    with WinError 5 on Windows.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    doc.save(str(tmp), **options)
    doc.close()
    os.replace(tmp, path)
    return fitz.open(str(path))


def close_quietly(doc: fitz.Document | None) -> None:
    if doc is not None:
        try:
            doc.close()
        except Exception:  # noqa: BLE001
            pass
