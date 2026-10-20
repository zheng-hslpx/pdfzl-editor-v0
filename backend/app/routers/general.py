"""General page-operation endpoints.

Ports Stirling-PDF's @GeneralApi group (/api/v1/general/*):
merge-pdfs, split-pages, rotate, remove-pages, reorder-pages.
Engine: PyMuPDF (PDFium-based, same lineage as Stirling's JPDFium).
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import fitz
from fastapi import APIRouter, File, Form, Header, UploadFile
from fastapi.responses import JSONResponse

from ..core.errors import PdfError
from ..core.tempfile import save_upload, temp_manager
from ..services.jobs import job_manager

router = APIRouter(prefix="/api/v1/general", tags=["general"])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _base_name(filename: str | None) -> str:
    return Path(filename or "document.pdf").stem


def _resolve_order(file_order: str, names: list[str]) -> list[int]:
    """Resolve fileOrder tokens (filenames, stems or 1-based indices)
    into a permutation of range(len(names)). Empty -> identity order."""
    tokens = [t.strip() for t in file_order.replace("\n", ",").split(",") if t.strip()]
    if not tokens:
        return list(range(len(names)))
    resolved: list[int] = []
    for token in tokens:
        matches = [i for i, n in enumerate(names) if n == token]
        if not matches:
            matches = [i for i, n in enumerate(names) if Path(n).stem == token]
        if not matches:
            try:
                idx = int(token) - 1
            except ValueError as exc:
                raise PdfError(400, f"无法识别的排序项: {token}") from exc
            if idx < 0 or idx >= len(names):
                raise PdfError(400, f"排序序号越界: {token}")
            matches = [idx]
        if len(matches) > 1:
            raise PdfError(400, f"排序项歧义（多个文件同名）: {token}")
        resolved.append(matches[0])
    if sorted(resolved) != list(range(len(names))):
        raise PdfError(400, "排序列表未覆盖全部文件或包含重复项")
    return resolved


def _parse_segments(spec: str, total: int) -> list[tuple[list[int], str]]:
    """Parse a split spec into (0-based pages, label) segments.

    Each comma-separated part is one output PDF: "N" or "N-M".
    "all" alone means the whole document as one file.
    """
    spec = (spec or "").strip()
    if not spec:
        raise PdfError(400, "拆分范围不能为空")
    if spec.lower() == "all":
        return [(list(range(total)), "all")]
    segments: list[tuple[list[int], str]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if part.lower() == "all":
            raise PdfError(400, "all 只能单独使用")
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                start, end = int(a), int(b)
            except ValueError as exc:
                raise PdfError(400, f"无效的拆分范围: {part}") from exc
            if start < 1 or end > total or start > end:
                raise PdfError(400, f"拆分范围越界或无效: {part}（文档共 {total} 页）")
            pages = list(range(start - 1, end))
            label = f"{start}-{end}" if start != end else str(start)
        else:
            try:
                page = int(part)
            except ValueError as exc:
                raise PdfError(400, f"无效的页码: {part}") from exc
            if page < 1 or page > total:
                raise PdfError(400, f"页码越界: {page}（文档共 {total} 页）")
            pages = [page - 1]
            label = str(page)
        segments.append((pages, label))
    if not segments:
        raise PdfError(400, "拆分范围解析结果为空")
    return segments


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------

@router.post("/merge-pdfs", summary="合并多个 PDF")
async def merge_pdfs(
    fileInput: list[UploadFile] = File(...),
    fileOrder: str = Form(default=""),
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    if len(fileInput) < 2:
        raise PdfError(400, "合并至少需要 2 个 PDF 文件")
    names = [f.filename or "file.pdf" for f in fileInput]
    order = _resolve_order(fileOrder, names)

    inputs = []
    try:
        for upload in fileInput:
            inputs.append(await save_upload(upload))
        payload = {
            "source_paths": [str(path) for path in inputs],
            "order": order,
            "names": names,
        }
        request_hash = job_manager.fingerprint(
            "general_merge", payload, [temp_manager.get_digest(path) for path in inputs]
        )
        job = await job_manager.submit(
            "general_merge",
            payload,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            input_paths=inputs,
        )
        return JSONResponse(status_code=202, content={"jobId": job.id, "state": job.state})
    except BaseException:
        for path in inputs:
            temp_manager.discard(path)
        raise


@router.post("/split-pages", summary="按页码范围拆分（输出 ZIP）")
async def split_pages(
    fileInput: UploadFile = File(...),
    pageRanges: str = Form(default=""),
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    src_path = await save_upload(fileInput)
    base = _base_name(fileInput.filename)
    try:
        payload = {
            "source_path": str(src_path),
            "filename": fileInput.filename or "document.pdf",
            "page_ranges": pageRanges,
        }
        request_hash = job_manager.fingerprint(
            "general_split", payload, [temp_manager.get_digest(src_path)]
        )
        job = await job_manager.submit(
            "general_split",
            payload,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            input_paths=[src_path],
        )
        return JSONResponse(status_code=202, content={"jobId": job.id, "state": job.state})
    except BaseException:
        temp_manager.discard(src_path)
        raise


@router.post("/rotate", summary="旋转页面")
async def rotate(
    fileInput: UploadFile = File(...),
    angle: int = Form(...),
    pages: str = Form(default="all"),
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    if angle not in (90, 180, 270):
        raise PdfError(400, "旋转角度仅支持 90 / 180 / 270")
    src_path = await save_upload(fileInput)
    try:
        payload = {
            "source_path": str(src_path),
            "filename": fileInput.filename or "document.pdf",
            "angle": angle,
            "pages": pages,
        }
        request_hash = job_manager.fingerprint(
            "general_rotate", payload, [temp_manager.get_digest(src_path)]
        )
        job = await job_manager.submit(
            "general_rotate", payload, idempotency_key=idempotency_key,
            request_hash=request_hash, input_paths=[src_path],
        )
        return JSONResponse(status_code=202, content={"jobId": job.id, "state": job.state})
    except BaseException:
        temp_manager.discard(src_path)
        raise


@router.post("/remove-pages", summary="删除指定页")
async def remove_pages(
    fileInput: UploadFile = File(...),
    pages: str = Form(...),
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    src_path = await save_upload(fileInput)
    try:
        payload = {
            "source_path": str(src_path),
            "filename": fileInput.filename or "document.pdf",
            "pages": pages,
        }
        request_hash = job_manager.fingerprint(
            "general_remove", payload, [temp_manager.get_digest(src_path)]
        )
        job = await job_manager.submit(
            "general_remove", payload, idempotency_key=idempotency_key,
            request_hash=request_hash, input_paths=[src_path],
        )
        return JSONResponse(status_code=202, content={"jobId": job.id, "state": job.state})
    except BaseException:
        temp_manager.discard(src_path)
        raise


@router.post("/reorder-pages", summary="重排页面顺序")
async def reorder_pages(
    fileInput: UploadFile = File(...),
    pageOrder: str = Form(...),
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    src_path = await save_upload(fileInput)
    try:
        payload = {
            "source_path": str(src_path),
            "filename": fileInput.filename or "document.pdf",
            "page_order": pageOrder,
        }
        request_hash = job_manager.fingerprint(
            "general_reorder", payload, [temp_manager.get_digest(src_path)]
        )
        job = await job_manager.submit(
            "general_reorder", payload, idempotency_key=idempotency_key,
            request_hash=request_hash, input_paths=[src_path],
        )
        return JSONResponse(status_code=202, content={"jobId": job.id, "state": job.state})
    except BaseException:
        temp_manager.discard(src_path)
        raise
