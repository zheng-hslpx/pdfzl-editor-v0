"""Metadata and small utility routes submit bounded background jobs."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, Header, UploadFile
from fastapi.responses import JSONResponse

from ..core.errors import PdfError
from ..core.tempfile import save_upload, temp_manager
from ..services.jobs import job_manager

router = APIRouter(prefix="/api/v1/misc", tags=["misc"])
PAGE_NUMBER_POSITIONS = {
    "bottom-center", "bottom-right", "bottom-left",
    "top-center", "top-right", "top-left",
}


def _accepted(job) -> JSONResponse:
    return JSONResponse(
        status_code=202,
        content={"jobId": job.id, "state": job.state},
    )


async def _submit_pdf(operation: str, upload: UploadFile, options: dict,
                      idempotency_key: str, *, allow_encrypted: bool = False) -> JSONResponse:
    path = await save_upload(upload, allow_encrypted=allow_encrypted)
    payload = {**options, "source_path": str(path), "filename": upload.filename or "document.pdf"}
    request_hash = job_manager.fingerprint(operation, payload, [temp_manager.get_digest(path)])
    try:
        job = await job_manager.submit(
            operation, payload,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            input_paths=[path],
        )
        return _accepted(job)
    except BaseException:
        temp_manager.discard(path)
        raise


@router.post("/pdf-info", summary="PDF 文档信息")
async def pdf_info(
    fileInput: UploadFile = File(...),
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    return await _submit_pdf(
        "misc_pdf_info", fileInput, {}, idempotency_key, allow_encrypted=True
    )


@router.post("/compress", summary="PDF 对象优化")
async def compress(
    fileInput: UploadFile = File(...),
    level: str = Form(default="medium"),
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    # The previous three values are accepted during the UI migration but map
    # to one truthful object-optimization operation; no image downsampling occurs.
    if level not in ("low", "medium", "high"):
        raise PdfError(400, "压缩选项无效")
    return await _submit_pdf("misc_compress", fileInput, {}, idempotency_key)


@router.post("/watermark", summary="添加文字水印")
async def watermark(
    fileInput: UploadFile = File(...),
    text: str = Form(...),
    opacity: float = Form(default=0.3),
    rotation: int = Form(default=45),
    fontSize: int = Form(default=48),
    color: str = Form(default="#888888"),
    pages: str = Form(default="all"),
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    if not text.strip():
        raise PdfError(400, "水印文字不能为空")
    if not (0 < opacity <= 1):
        raise PdfError(400, "透明度需在 0-1 之间")
    if not (6 <= fontSize <= 200):
        raise PdfError(400, "字号需在 6-200 之间")
    if not (0 <= rotation <= 360):
        raise PdfError(400, "旋转角度需在 0-360 之间")
    options = {
        "text": text,
        "opacity": opacity,
        "rotation": rotation,
        "fontSize": fontSize,
        "color": color,
        "pages": pages,
    }
    return await _submit_pdf("misc_watermark", fileInput, options, idempotency_key)


@router.post("/add-page-numbers", summary="添加页码")
async def add_page_numbers(
    fileInput: UploadFile = File(...),
    position: str = Form(default="bottom-center"),
    startNumber: int = Form(default=1),
    fontSize: int = Form(default=10),
    margin: int = Form(default=36),
    pages: str = Form(default="all"),
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    if position not in PAGE_NUMBER_POSITIONS:
        raise PdfError(400, f"不支持的位置: {position}")
    if not (6 <= fontSize <= 48):
        raise PdfError(400, "字号需在 6-48 之间")
    if not (0 <= margin <= 200):
        raise PdfError(400, "边距需在 0-200 之间")
    options = {
        "position": position,
        "startNumber": startNumber,
        "fontSize": fontSize,
        "margin": margin,
        "pages": pages,
    }
    return await _submit_pdf("misc_page_numbers", fileInput, options, idempotency_key)
