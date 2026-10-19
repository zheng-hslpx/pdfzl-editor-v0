"""Conversion endpoints (simplified Stirling-PDF @ConvertApi group).

- /convert/pdf-to-image    PyMuPDF pixmap rendering (png/jpeg, dpi)
- /convert/pdf-to-text     PyMuPDF text extraction
- /convert/pdf-to-word     pdf2docx (best-effort)
- /convert/office-to-pdf   LibreOffice headless
- /convert/image-to-pdf    PyMuPDF (a4/letter/source page sizing)
"""
from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path

import fitz
from fastapi import APIRouter, File, Form, UploadFile

from ..core import pdf_engine
from ..core.errors import PdfError
from ..core.responses import file_response
from ..core.tempfile import save_upload, temp_manager
from ..services import libreoffice
from ..services.pdf2docx_service import DOCX_MEDIA_TYPE, convert_pdf_to_docx

router = APIRouter(prefix="/api/v1/convert", tags=["convert"])

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".tif"}


def _base_name(filename: str | None) -> str:
    return Path(filename or "document.pdf").stem


@router.post("/pdf-to-image", summary="PDF 渲染为图片")
async def pdf_to_image(
    fileInput: UploadFile = File(...),
    format: str = Form(default="png"),
    dpi: int = Form(default=150),
    pages: str = Form(default="all"),
):
    fmt = format.lower().lstrip(".")
    if fmt in ("jpg",):
        fmt = "jpeg"
    if fmt not in ("png", "jpeg"):
        raise PdfError(400, "图片格式仅支持 png / jpeg")
    if not (36 <= dpi <= 600):
        raise PdfError(400, "DPI 需在 36-600 之间")

    src_path = await save_upload(fileInput)
    doc = pdf_engine.open_pdf(src_path)
    base = _base_name(fileInput.filename)
    try:
        indices = pdf_engine.parse_page_ranges(pages, doc.page_count)
        ext = "png" if fmt == "png" else "jpg"
        images: list[tuple[str, bytes]] = []
        for i in indices:
            zoom = dpi / 72.0
            pix = doc[i].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            images.append((f"page_{i + 1}.{ext}", pix.tobytes(ext)))

        if len(images) == 1:
            dest = temp_manager.create_temp_file(f".{ext}")
            dest.write_bytes(images[0][1])
            first = indices[0] + 1
            return file_response(
                dest,
                f"{base}_page_{first}.{ext}",
                media_type="image/png" if fmt == "png" else "image/jpeg",
            )

        zip_path = temp_manager.create_temp_file(".zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in images:
                zf.writestr(f"{base}_{name}", data)
        return file_response(zip_path, f"{base}_images.zip", media_type="application/zip")
    finally:
        doc.close()
        temp_manager.discard(src_path)


@router.post("/pdf-to-text", summary="PDF 提取纯文本")
async def pdf_to_text(fileInput: UploadFile = File(...)):
    src_path = await save_upload(fileInput)
    doc = pdf_engine.open_pdf(src_path)
    base = _base_name(fileInput.filename)
    try:
        text = "\n\n".join(doc[i].get_text("text") for i in range(doc.page_count))
        dest = temp_manager.create_temp_file(".txt")
        dest.write_text(text, encoding="utf-8")
    finally:
        doc.close()
        temp_manager.discard(src_path)
    return file_response(dest, f"{base}.txt", media_type="text/plain; charset=utf-8")


@router.post("/pdf-to-word", summary="PDF 转 Word（best-effort）")
async def pdf_to_word(fileInput: UploadFile = File(...)):
    src_path = await save_upload(fileInput)
    base = _base_name(fileInput.filename)
    doc = pdf_engine.open_pdf(src_path)  # validate only
    doc.close()
    try:
        dest = await asyncio.to_thread(convert_pdf_to_docx, src_path)
    finally:
        temp_manager.discard(src_path)
    return file_response(dest, f"{base}.docx", media_type=DOCX_MEDIA_TYPE)


@router.post("/office-to-pdf", summary="Office 文档转 PDF（LibreOffice）")
async def office_to_pdf(fileInput: UploadFile = File(...)):
    name = fileInput.filename or "document.docx"
    ext = Path(name).suffix.lower()
    if ext not in libreoffice.OFFICE_EXTENSIONS:
        supported = "、".join(sorted(libreoffice.OFFICE_EXTENSIONS))
        raise PdfError(400, f"不支持的格式 {ext or '（无扩展名）'}，支持：{supported}")
    src_path = await save_upload(fileInput, suffix=ext)
    try:
        dest = await libreoffice.convert_to_pdf(src_path)
    finally:
        temp_manager.discard(src_path)
    return file_response(dest, f"{Path(name).stem}.pdf")


@router.post("/image-to-pdf", summary="图片合并为 PDF")
async def image_to_pdf(
    fileInput: list[UploadFile] = File(...),
    pageSize: str = Form(default="a4"),
    fit: bool = Form(default=True),
):
    if not fileInput:
        raise PdfError(400, "请至少上传一张图片")
    if pageSize not in ("a4", "letter", "source"):
        raise PdfError(400, "页面尺寸仅支持 a4 / letter / source")

    inputs = []
    for f in fileInput:
        ext = Path(f.filename or "image.png").suffix.lower() or ".png"
        if ext not in IMAGE_EXTENSIONS:
            raise PdfError(400, f"不支持的图片格式: {f.filename}")
        inputs.append(await save_upload(f, suffix=ext))

    out = fitz.open()
    try:
        for path in inputs:
            try:
                img = fitz.open(str(path))
            except Exception as exc:  # noqa: BLE001
                raise PdfError(400, f"无法读取图片 {path.name}: {exc}") from exc
            try:
                if pageSize == "source":
                    pix = fitz.Pixmap(str(path))
                    page = out.new_page(width=pix.width, height=pix.height)
                    pix = None
                    page.insert_image(page.rect, filename=str(path), keep_proportion=fit)
                else:
                    target = fitz.paper_rect(pageSize)
                    page = out.new_page(width=target.width, height=target.height)
                    margin = 36
                    avail = fitz.Rect(
                        margin, margin, target.width - margin, target.height - margin
                    )
                    page.insert_image(
                        avail,
                        filename=str(path),
                        keep_proportion=fit,
                    )
            finally:
                img.close()

        dest = temp_manager.create_temp_file(".pdf")
        pdf_engine.save_atomic(out, dest, garbage=4, deflate=True)
    finally:
        out.close()
        for path in inputs:
            temp_manager.discard(path)

    first = Path(fileInput[0].filename or "images").stem
    return file_response(dest, f"{first}_images.pdf")
