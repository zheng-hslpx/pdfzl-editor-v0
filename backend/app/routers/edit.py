"""Visual editing endpoints: docId sessions + line-level text model.

Design (simplified from Stirling-PDF's jobId text-editor flow):
- POST /open            upload PDF -> docId + page geometry + textModel
                        (line-level editable units with bbox/font/color)
- POST /{id}/apply      text replacements (empty text = whiteout):
                        redact original line -> reinsert with auto font
                        and auto-shrinking font size
- POST /{id}/text/add   insert new text at a point
- POST /{id}/image/add  insert an image (stamp) into a rect
- POST /{id}/pages/*    rotate / delete / move pages (return fresh model)
- GET  /{id}/download   save-as stream
- POST /{id}/close      release the session immediately

Coordinates are PDF points in fitz space (origin top-left, y down) —
which maps to the pdf.js viewport by uniform scaling, no flip needed.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import fitz
from fastapi import APIRouter, File, Form, Header, Query, UploadFile
from fastapi.responses import JSONResponse

from ..core import pdf_engine
from ..core.errors import PdfError
from ..core.input_validation import validate_image
from ..core.responses import file_response
from ..core.tempfile import save_upload, temp_manager
from ..services.sessions import session_manager
from ..services.jobs import job_manager
from ..schemas import (
    AddTextRequest,
    ApplyEditsRequest,
    DeletePagesRequest,
    MovePageRequest,
    RotatePageRequest,
)

router = APIRouter(prefix="/api/v1/edit", tags=["edit"])


# ---------------------------------------------------------------------------
# text model
# ---------------------------------------------------------------------------

def _map_font(font_name: str, text: str) -> str:
    lowered = font_name.lower()
    if any(ord(c) > 0x2E80 for c in text):
        return pdf_engine.FONT_CJK
    if "bold" in lowered:
        return pdf_engine.FONT_HEBO
    if "italic" in lowered or "oblique" in lowered:
        return pdf_engine.FONT_HEIT
    return pdf_engine.FONT_HELV


def _line_color(color_int: int) -> str:
    r = (color_int >> 16) & 0xFF
    g = (color_int >> 8) & 0xFF
    b = color_int & 0xFF
    return f"#{r:02x}{g:02x}{b:02x}"


def extract_page_text_model(doc: fitz.Document, pno: int, page_id: str) -> dict:
    """Extract editable text for one page without scanning the full document."""
    page = doc[pno]
    lines: list[dict] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            spans = [s for s in line["spans"] if s.get("text")]
            if not spans:
                continue
            text = "".join(s["text"] for s in spans)
            if not text.strip():
                continue
            first = spans[0]
            lines.append(
                {
                    "id": f"{page_id}:l{len(lines)}",
                    "pageId": page_id,
                    "originalHash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "bbox": [round(v, 2) for v in line["bbox"]],
                    "fontSize": round(float(first.get("size", 12)), 1),
                    "font": _map_font(str(first.get("font", "")), text),
                    "color": _line_color(int(first.get("color", 0))),
                    "text": text,
                }
            )
    return {
        "page": pno,
        "pageId": page_id,
        "width": round(page.rect.width, 2),
        "height": round(page.rect.height, 2),
        "rotation": page.rotation,
        "lines": lines,
    }


def extract_text_model(
    doc: fitz.Document, page_ids: list[str] | None = None
) -> list[dict]:
    """Extract line-level editable units per page."""
    return [
        extract_page_text_model(
            doc,
            pno,
            page_ids[pno] if page_ids and pno < len(page_ids) else f"page-{pno}",
        )
        for pno in range(doc.page_count)
    ]


def _page_meta(doc: fitz.Document, page_ids: list[str] | None = None) -> list[dict]:
    return [
        {
            "page": i,
            "pageId": page_ids[i] if page_ids and i < len(page_ids) else f"page-{i}",
            "width": round(doc[i].rect.width, 2),
            "height": round(doc[i].rect.height, 2),
            "rotation": doc[i].rotation,
        }
        for i in range(doc.page_count)
    ]


def _load_session_pdf(doc_id: str) -> fitz.Document:
    session = session_manager.get(doc_id)
    return pdf_engine.open_pdf(session.path)


def _check_revision(session, expected_revision: int) -> None:
    if session.closed:
        raise PdfError(404, "编辑会话不存在或已过期，请重新打开文件")
    if session.revision != expected_revision:
        raise PdfError(
            409,
            "文档已被其他操作修改，请刷新页面后重新定位草稿",
            code="REVISION_CONFLICT",
        )


def _check_page(session, page_no: int, page_id: str, doc: fitz.Document) -> fitz.Page:
    if page_no < 0 or page_no >= doc.page_count:
        raise PdfError(400, f"页码越界: {page_no}")
    if page_no >= len(session.page_ids) or session.page_ids[page_no] != page_id:
        raise PdfError(409, "页面身份已变化，请重新定位操作", code="REVISION_CONFLICT")
    page = doc[page_no]
    if page.rotation != 0:
        raise PdfError(
            422,
            "旋转页暂不支持坐标编辑，请先用旋转工具归一化",
            code="ROTATED_PAGE_UNSUPPORTED",
        )
    return page


def _check_line_anchor(session, doc: fitz.Document, edit) -> None:
    page = _check_page(session, edit.page, edit.pageId or "", doc)
    if edit.kind == "cover":
        return
    page_model = extract_page_text_model(doc, edit.page, edit.pageId or "")
    line = next((item for item in page_model["lines"] if item["id"] == edit.lineId), None)
    if (
        line is None
        or line["originalHash"] != edit.originalHash
        or line["text"] != edit.original
        or any(abs(float(a) - float(b)) > 0.5 for a, b in zip(line["bbox"], edit.bbox))
    ):
        raise PdfError(409, "文本行已变化，请重新定位草稿", code="REVISION_CONFLICT")


def _ensure_session_writable(session, expected_revision: int) -> None:
    _check_revision(session, expected_revision)
    if session.pending_job_id is not None:
        raise PdfError(409, "文档修改任务仍在执行", code="SESSION_BUSY")


def _preflight_page_write(
    session, expected_revision: int, page_no: int, page_id: str,
    rect: fitz.Rect | None = None,
) -> None:
    """Keep stale revision, page identity, and rotation errors as HTTP 4xx."""
    with session.lock:
        _ensure_session_writable(session, expected_revision)
        doc = _load_session_pdf(session.id)
        try:
            page = _check_page(session, page_no, page_id, doc)
            if rect is not None and not page.rect.contains(rect):
                raise PdfError(422, "编辑区域超出页面范围", code="INVALID_BBOX")
        finally:
            doc.close()


def _preflight_edits(session, payload: ApplyEditsRequest) -> None:
    with session.lock:
        _ensure_session_writable(session, payload.expectedRevision)
        doc = _load_session_pdf(session.id)
        try:
            for edit in payload.edits:
                _check_line_anchor(session, doc, edit)
                if not doc[edit.page].rect.contains(fitz.Rect(edit.bbox)):
                    raise PdfError(422, "编辑区域超出页面范围", code="INVALID_BBOX")
        finally:
            doc.close()


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------

@router.post("/open", summary="打开 PDF 进入编辑会话")
async def open_document(
    fileInput: UploadFile = File(...),
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    src_path = await save_upload(fileInput)
    session = None
    try:
        digest = temp_manager.get_digest(src_path)
        page_count = temp_manager.get_page_count(src_path)
        session = session_manager.create(
            src_path, fileInput.filename or "document.pdf", page_count
        )
        temp_manager.discard(src_path)
        payload = {
            "source_path": str(session.path),
            "page_ids": list(session.page_ids),
            "docId": session.id,
            "originalName": session.original_name,
            "revision": session.revision,
        }
        request_hash = job_manager.fingerprint(
            "editor_open", {"originalName": session.original_name}, [digest]
        )
        job = await job_manager.submit(
            "editor_open",
            payload,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            session_id=session.id,
            cleanup_session_on_failure=True,
            idempotency_scope="local",
        )
        return JSONResponse(status_code=202, content={"jobId": job.id, "state": job.state})
    except BaseException:
        if session is not None:
            session_manager.close(session.id)
        temp_manager.discard(src_path)
        raise


@router.post("/{doc_id}/apply", summary="应用文本编辑（空文本=涂抹）")
async def apply_edits(
    doc_id: str,
    payload: ApplyEditsRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    session = session_manager.get(doc_id)
    _preflight_edits(session, payload)
    payload_dict = payload.model_dump()
    payload_dict.update({"source_path": str(session.path), "page_ids": list(session.page_ids)})
    request_hash = job_manager.fingerprint(
        "editor_apply", {**payload_dict, "docId": doc_id}, []
    )
    job = await job_manager.submit(
        "editor_apply",
        payload_dict,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        session_id=doc_id,
        expected_revision=payload.expectedRevision,
    )
    return JSONResponse(status_code=202, content={"jobId": job.id, "state": job.state})


@router.post("/{doc_id}/text/add", summary="添加文字")
async def add_text(
    doc_id: str,
    payload: AddTextRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    session = session_manager.get(doc_id)
    _preflight_page_write(
        session, payload.expectedRevision, payload.page, payload.pageId,
        fitz.Rect(payload.x, payload.y, payload.x + 1, payload.y + 1),
    )
    payload_dict = payload.model_dump()
    payload_dict.update({"source_path": str(session.path), "page_ids": list(session.page_ids)})
    request_hash = job_manager.fingerprint(
        "editor_add_text", {**payload_dict, "docId": doc_id}, []
    )
    job = await job_manager.submit(
        "editor_add_text",
        payload_dict,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        session_id=doc_id,
        expected_revision=payload.expectedRevision,
    )
    return JSONResponse(status_code=202, content={"jobId": job.id, "state": job.state})


@router.post("/{doc_id}/image/add", summary="添加图片/印章")
async def add_image(
    doc_id: str,
    image: UploadFile = File(...),
    page: int = Form(...),
    x: float = Form(...),
    y: float = Form(...),
    w: float = Form(...),
    h: float = Form(...),
    expectedRevision: int = Form(..., ge=0),
    pageId: str = Form(...),
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
        raise PdfError(400, "图片尺寸必须为正数")
    session = session_manager.get(doc_id)
    img_path = await save_upload(image, suffix=Path(image.filename or "stamp.png").suffix or ".png")
    try:
        validate_image(img_path)
        _preflight_page_write(
            session, expectedRevision, page, pageId,
            fitz.Rect(x, y, x + w, y + h),
        )
        payload = {
            "source_path": str(session.path),
            "image_path": str(img_path),
            "page_ids": list(session.page_ids),
            "page": page,
            "pageId": pageId,
            "x": x,
            "y": y,
            "w": w,
            "h": h,
        }
        request_hash = job_manager.fingerprint(
            "editor_add_image", {**payload, "docId": doc_id, "expectedRevision": expectedRevision},
            [temp_manager.get_digest(img_path)],
        )
        job = await job_manager.submit(
            "editor_add_image",
            payload,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            input_paths=[img_path],
            session_id=doc_id,
            expected_revision=expectedRevision,
        )
        return JSONResponse(status_code=202, content={"jobId": job.id, "state": job.state})
    except BaseException:
        temp_manager.discard(img_path)
        raise


@router.post("/{doc_id}/pages/rotate", summary="旋转页面")
async def rotate_page(
    doc_id: str,
    payload: RotatePageRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    page_no, angle = payload.page, payload.angle
    if angle not in (90, 180, 270):
        raise PdfError(400, "旋转角度仅支持 90/180/270")
    session = session_manager.get(doc_id)
    payload_dict = {
        "source_path": str(session.path),
        "page_ids": list(session.page_ids),
        "page": page_no,
        "angle": angle,
    }
    request_hash = job_manager.fingerprint(
        "editor_rotate", {**payload_dict, "docId": doc_id, "expectedRevision": payload.expectedRevision}, []
    )
    job = await job_manager.submit(
        "editor_rotate", payload_dict, idempotency_key=idempotency_key,
        request_hash=request_hash, session_id=doc_id,
        expected_revision=payload.expectedRevision,
    )
    return JSONResponse(status_code=202, content={"jobId": job.id, "state": job.state})


@router.post("/{doc_id}/pages/delete", summary="删除页面")
async def delete_pages(
    doc_id: str,
    payload: DeletePagesRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    pages = payload.pages
    session = session_manager.get(doc_id)
    payload_dict = {
        "source_path": str(session.path),
        "page_ids": list(session.page_ids),
        "pages": pages,
    }
    request_hash = job_manager.fingerprint(
        "editor_delete", {**payload_dict, "docId": doc_id, "expectedRevision": payload.expectedRevision}, []
    )
    job = await job_manager.submit(
        "editor_delete", payload_dict, idempotency_key=idempotency_key,
        request_hash=request_hash, session_id=doc_id,
        expected_revision=payload.expectedRevision,
    )
    return JSONResponse(status_code=202, content={"jobId": job.id, "state": job.state})


@router.post("/{doc_id}/pages/move", summary="移动页面")
async def move_page(
    doc_id: str,
    payload: MovePageRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    src, dst = payload.fromPage, payload.toPage
    session = session_manager.get(doc_id)
    payload_dict = {
        "source_path": str(session.path),
        "page_ids": list(session.page_ids),
        "from": src,
        "to": dst,
    }
    request_hash = job_manager.fingerprint(
        "editor_move", {**payload_dict, "docId": doc_id, "expectedRevision": payload.expectedRevision}, []
    )
    job = await job_manager.submit(
        "editor_move", payload_dict, idempotency_key=idempotency_key,
        request_hash=request_hash, session_id=doc_id,
        expected_revision=payload.expectedRevision,
    )
    return JSONResponse(status_code=202, content={"jobId": job.id, "state": job.state})


@router.get("/{doc_id}/download", summary="另存为下载")
async def download(doc_id: str, filename: str = Query(default="")):
    session = session_manager.get(doc_id)
    with session.lock:
        if session.pending_job_id is not None:
            raise PdfError(409, "文档修改任务仍在执行，请等待任务完成", code="SESSION_BUSY")
    name = filename.strip() or session.original_name
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    # Session files are owned by the session lifecycle (close / TTL),
    # never deleted after a single response.
    return file_response(session.path, name, delete_after=False)


@router.post("/{doc_id}/close", summary="关闭编辑会话")
async def close_session(doc_id: str):
    session = session_manager._sessions.get(doc_id)
    if session is not None:
        with session.lock:
            if session.pending_job_id is not None:
                raise PdfError(409, "文档修改任务仍在执行，请先等待或取消任务", code="SESSION_BUSY")
            session_manager.close(doc_id)
    return {"ok": True}


@router.post("/{doc_id}/touch", summary="续期编辑会话")
async def touch_session(doc_id: str):
    session_manager.touch(doc_id)
    return {"ok": True}


def _parse_color(value: str) -> tuple[float, float, float]:
    s = value.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        raise PdfError(400, f"无效的颜色值: {value}")
    try:
        return tuple(int(s[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError as exc:
        raise PdfError(400, f"无效的颜色值: {value}") from exc
