"""Conversion endpoints submit bounded background jobs."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, Header, UploadFile
from fastapi.responses import JSONResponse

from ..core.errors import PdfError
from ..core.input_validation import validate_image
from ..core.tempfile import save_upload, temp_manager
from ..services.jobs import job_manager
from ..services import libreoffice

router = APIRouter(prefix="/api/v1/convert", tags=["convert"])
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".tif"}


def _accepted(job) -> JSONResponse:
    return JSONResponse(
        status_code=202,
        content={"jobId": job.id, "state": job.state},
    )


async def _submit_one(
    operation: str,
    upload: UploadFile,
    options: dict,
    idempotency_key: str,
    suffix: str = ".pdf",
) -> JSONResponse:
    path = await save_upload(upload, suffix=suffix)
    payload = {**options, "source_path": str(path), "filename": upload.filename or "document"}
    digest = temp_manager.get_digest(path)
    request_hash = job_manager.fingerprint(operation, payload, [digest])
    try:
        job = await job_manager.submit(
            operation,
            payload,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            input_paths=[path],
        )
        return _accepted(job)
    except BaseException:
        temp_manager.discard(path)
        raise


@router.post("/pdf-to-image", summary="PDF 渲染为图片")
async def pdf_to_image(
    fileInput: UploadFile = File(...),
    format: str = Form(default="png"),
    dpi: int = Form(default=150),
    pages: str = Form(default="all"),
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    fmt = format.lower().lstrip(".")
    if fmt == "jpg":
        fmt = "jpeg"
    if fmt not in ("png", "jpeg"):
        raise PdfError(400, "图片格式仅支持 png / jpeg")
    if not (36 <= dpi <= 600):
        raise PdfError(400, "DPI 需在 36-600 之间")
    return await _submit_one(
        "convert_pdf_to_image",
        fileInput,
        {"format": fmt, "dpi": dpi, "pages": pages},
        idempotency_key,
    )


@router.post("/pdf-to-text", summary="PDF 提取纯文本")
async def pdf_to_text(
    fileInput: UploadFile = File(...),
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    return await _submit_one("convert_pdf_to_text", fileInput, {}, idempotency_key)


@router.post("/pdf-to-word", summary="PDF 转 Word（best-effort）")
async def pdf_to_word(
    fileInput: UploadFile = File(...),
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    return await _submit_one("convert_pdf_to_word", fileInput, {}, idempotency_key)


@router.post("/office-to-pdf", summary="Office 文档转 PDF（LibreOffice）")
async def office_to_pdf(
    fileInput: UploadFile = File(...),
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    name = fileInput.filename or "document.docx"
    ext = Path(name).suffix.lower()
    if ext not in libreoffice.OFFICE_EXTENSIONS:
        supported = "、".join(sorted(libreoffice.OFFICE_EXTENSIONS))
        raise PdfError(400, f"不支持的格式 {ext or '（无扩展名）'}，支持：{supported}")
    return await _submit_one(
        "office_to_pdf",
        fileInput,
        {},
        idempotency_key,
        suffix=ext,
    )


@router.post("/image-to-pdf", summary="图片合并为 PDF")
async def image_to_pdf(
    fileInput: list[UploadFile] = File(...),
    pageSize: str = Form(default="a4"),
    fit: bool = Form(default=True),
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    if not fileInput:
        raise PdfError(400, "请至少上传一张图片")
    if pageSize not in ("a4", "letter", "source"):
        raise PdfError(400, "页面尺寸仅支持 a4 / letter / source")

    paths: list[Path] = []
    submitted = False
    try:
        for upload in fileInput:
            ext = Path(upload.filename or "image.png").suffix.lower() or ".png"
            if ext not in IMAGE_EXTENSIONS:
                raise PdfError(400, "不支持的图片格式", code="INVALID_IMAGE")
            path = await save_upload(upload, suffix=ext)
            paths.append(path)
            validate_image(path)

        names = [upload.filename or "image.png" for upload in fileInput]
        payload = {
            "image_paths": [str(path) for path in paths],
            "pageSize": pageSize,
            "fit": fit,
            "filename": names[0],
        }
        hashes = [temp_manager.get_digest(path) for path in paths]
        request_hash = job_manager.fingerprint("convert_image_to_pdf", payload, hashes)
        job = await job_manager.submit(
            "convert_image_to_pdf",
            payload,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            input_paths=paths,
        )
        submitted = True
        return _accepted(job)
    finally:
        if not submitted:
            for path in paths:
                temp_manager.discard(path)
