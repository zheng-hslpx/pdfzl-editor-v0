"""Misc endpoints (simplified Stirling-PDF @MiscApi group).

- /misc/pdf-info           document metadata / structure as JSON
- /misc/compress           three-level compression
- /misc/watermark          diagonal text watermark
- /misc/add-page-numbers   page numbers at a chosen position
"""
from __future__ import annotations

from pathlib import Path

import fitz
from fastapi import APIRouter, File, Form, UploadFile

from ..core import pdf_engine
from ..core.errors import PdfError
from ..core.responses import file_response
from ..core.tempfile import save_upload, temp_manager

router = APIRouter(prefix="/api/v1/misc", tags=["misc"])

PAGE_NUMBER_POSITIONS = {
    "bottom-center",
    "bottom-right",
    "bottom-left",
    "top-center",
    "top-right",
    "top-left",
}


def _base_name(filename: str | None) -> str:
    return Path(filename or "document.pdf").stem


def _parse_color(value: str) -> tuple[float, float, float]:
    s = value.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        raise PdfError(400, f"无效的颜色值: {value}（应为 #RRGGBB）")
    try:
        return tuple(int(s[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError as exc:
        raise PdfError(400, f"无效的颜色值: {value}") from exc


@router.post("/pdf-info", summary="PDF 文档信息")
async def pdf_info(fileInput: UploadFile = File(...)):
    src_path = await save_upload(fileInput)
    doc = pdf_engine.open_pdf(src_path)
    try:
        fonts: set[str] = set()
        for i in range(doc.page_count):
            for f in doc[i].get_fonts():
                if len(f) > 3 and f[3]:
                    fonts.add(f[3])
        page = doc[0]
        return {
            "pageCount": doc.page_count,
            "pageSize": {
                "width": round(page.rect.width, 2),
                "height": round(page.rect.height, 2),
                "rotation": page.rotation,
            },
            "pdfVersion": doc.metadata.get("format", ""),
            "metadata": doc.metadata,
            "isEncrypted": doc.is_encrypted,
            "fonts": sorted(fonts),
            "tocEntries": len(doc.get_toc()),
            "fileSize": src_path.stat().st_size,
        }
    finally:
        doc.close()
        temp_manager.discard(src_path)


@router.post("/compress", summary="压缩 PDF")
async def compress(fileInput: UploadFile = File(...), level: str = Form(default="medium")):
    if level not in ("low", "medium", "high"):
        raise PdfError(400, "压缩级别仅支持 low / medium / high")
    src_path = await save_upload(fileInput)
    doc = pdf_engine.open_pdf(src_path)
    base = _base_name(fileInput.filename)
    try:
        dest = temp_manager.create_temp_file(".pdf")
        if level == "low":
            pdf_engine.save_atomic(doc, dest, garbage=3, deflate=True)
        elif level == "medium":
            pdf_engine.save_atomic(doc, dest, garbage=4, deflate=True, clean=True)
        else:
            try:
                doc.subset_fonts()
            except Exception:  # noqa: BLE001 - subsetting is best-effort
                pass
            pdf_engine.save_atomic(doc, dest, garbage=4, deflate=True, clean=True)
    finally:
        doc.close()
        temp_manager.discard(src_path)
    return file_response(dest, f"{base}_compressed.pdf")


@router.post("/watermark", summary="添加文字水印")
async def watermark(
    fileInput: UploadFile = File(...),
    text: str = Form(...),
    opacity: float = Form(default=0.3),
    rotation: int = Form(default=45),
    fontSize: int = Form(default=48),
    color: str = Form(default="#888888"),
    pages: str = Form(default="all"),
):
    if not text.strip():
        raise PdfError(400, "水印文字不能为空")
    if not (0 < opacity <= 1):
        raise PdfError(400, "透明度需在 0-1 之间")
    if not (6 <= fontSize <= 200):
        raise PdfError(400, "字号需在 6-200 之间")
    if not (0 <= rotation <= 360):
        raise PdfError(400, "旋转角度需在 0-360 之间")
    rgb = _parse_color(color)
    font = pdf_engine.pick_font(text)

    src_path = await save_upload(fileInput)
    doc = pdf_engine.open_pdf(src_path)
    base = _base_name(fileInput.filename)
    try:
        indices = pdf_engine.parse_page_ranges(pages, doc.page_count)
        tw = fitz.get_text_length(text, fontname=font, fontsize=fontSize)
        for i in indices:
            page = doc[i]
            r = page.rect
            cx, cy = r.width / 2, r.height / 2
            box = fitz.Rect(
                cx - tw / 2 - 20, cy - fontSize, cx + tw / 2 + 20, cy + fontSize
            )
            page.insert_textbox(
                box,
                text,
                fontname=font,
                fontsize=fontSize,
                color=rgb,
                align=fitz.TEXT_ALIGN_CENTER,
                overlay=True,
                fill_opacity=opacity,
                morph=(fitz.Point(cx, cy), fitz.Matrix(rotation)),
            )
        dest = temp_manager.create_temp_file(".pdf")
        pdf_engine.save_atomic(doc, dest, garbage=4, deflate=True)
    finally:
        doc.close()
        temp_manager.discard(src_path)
    return file_response(dest, f"{base}_watermarked.pdf")


@router.post("/add-page-numbers", summary="添加页码")
async def add_page_numbers(
    fileInput: UploadFile = File(...),
    position: str = Form(default="bottom-center"),
    startNumber: int = Form(default=1),
    fontSize: int = Form(default=10),
    margin: int = Form(default=36),
    pages: str = Form(default="all"),
):
    if position not in PAGE_NUMBER_POSITIONS:
        raise PdfError(400, f"不支持的位置: {position}")
    if not (6 <= fontSize <= 48):
        raise PdfError(400, "字号需在 6-48 之间")
    if not (0 <= margin <= 200):
        raise PdfError(400, "边距需在 0-200 之间")

    src_path = await save_upload(fileInput)
    doc = pdf_engine.open_pdf(src_path)
    base = _base_name(fileInput.filename)
    try:
        indices = pdf_engine.parse_page_ranges(pages, doc.page_count)
        for offset, i in enumerate(indices):
            page = doc[i]
            r = page.rect
            label = str(startNumber + offset)
            tw = fitz.get_text_length(label, fontname="helv", fontsize=fontSize)
            if position.startswith("bottom"):
                y = r.height - margin
            else:
                y = margin + fontSize
            if position.endswith("center"):
                x = (r.width - tw) / 2
            elif position.endswith("right"):
                x = r.width - margin - tw
            else:
                x = margin
            page.insert_text(
                (x, y),
                label,
                fontname="helv",
                fontsize=fontSize,
                color=(0.35, 0.35, 0.35),
            )
        dest = temp_manager.create_temp_file(".pdf")
        pdf_engine.save_atomic(doc, dest, garbage=4, deflate=True)
    finally:
        doc.close()
        temp_manager.discard(src_path)
    return file_response(dest, f"{base}_numbered.pdf")
