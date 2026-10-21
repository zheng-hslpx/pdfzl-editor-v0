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
    HistoryActionRequest,
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


def _accepted(job) -> JSONResponse:
    return JSONResponse(status_code=202, content={"jobId": job.id, "state": job.state})


def _check_revision(session, expected_revision: int) -> None:
    if session.closed:
        raise PdfError(404, "编辑会话不存在或已过期，请重新打开文件")
    if session.revision != expected_revision:
        raise PdfError(
            409,
            "文档已被其他操作修改，请刷新页面后重新定位草稿",
            code="REVISION_CONFLICT",
        )


def _ensure_session_writable(session, expected_revision: int) -> None:
    _check_revision(session, expected_revision)
    if session.pending_job_id is not None:
        raise PdfError(409, "文档修改任务仍在执行", code="SESSION_BUSY")


@router.get("/{doc_id}/pages", summary="分页读取页面信息")
async def get_pages(
    doc_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
):
    session = session_manager.get(doc_id)
    with session.lock:
        if session.pending_job_id is not None:
            raise PdfError(409, "文档修改任务仍在执行", code="SESSION_BUSY")
        metadata = session.page_metadata
        if not metadata:
            raise PdfError(409, "页面信息仍在准备", code="SESSION_BUSY")
        return {
            "docId": doc_id,
            "revision": session.revision,
            "pageCount": len(session.page_ids),
            "offset": offset,
            "limit": limit,
            "pages": metadata[offset : offset + limit],
        }


@router.get("/{doc_id}/pages/{page_no}/text", summary="读取单页文本模型")
async def get_page_text(doc_id: str, page_no: int):
    session = session_manager.get(doc_id)
    with session.lock:
        if session.pending_job_id is not None:
            raise PdfError(409, "文档修改任务仍在执行", code="SESSION_BUSY")
        if page_no < 0 or page_no >= len(session.page_ids):
            raise PdfError(404, "页面不存在")
        revision = session.revision
        page_id = session.page_ids[page_no]
        source_path = session.path
        session.active_text_reads += 1
    try:
        result = await job_manager.run_internal(
            "editor_page_text",
            {"source_path": str(source_path), "page": page_no, "page_id": page_id},
        )
        with session.lock:
            if session.closed:
                raise PdfError(404, "编辑会话不存在或已过期，请重新打开文件")
            if session.revision != revision:
                raise PdfError(
                    409,
                    "文档已被其他操作修改，请刷新页面后重新定位草稿",
                    code="REVISION_CONFLICT",
                )
            if session.pending_job_id is not None:
                raise PdfError(409, "文档修改任务仍在执行", code="SESSION_BUSY")
        return {"docId": doc_id, "revision": revision, "page": result["page"]}
    finally:
        with session.lock:
            session.active_text_reads = max(0, session.active_text_reads - 1)


@router.get("/{doc_id}/history", summary="查询撤销/重做状态")
async def get_history(doc_id: str):
    session = session_manager.get(doc_id)
    with session.lock:
        return {
            "docId": doc_id,
            "revision": session.revision,
            "canUndo": bool(session.undo_stack),
            "canRedo": bool(session.redo_stack),
            "undoSteps": len(session.undo_stack),
            "redoSteps": len(session.redo_stack),
            "historyBytes": sum(entry.size_bytes for entry in session.undo_stack + session.redo_stack),
            "maxSteps": 10,
            "maxBytes": 1024 * 1024 * 1024,
        }


@router.get("/{doc_id}/history/plan", summary="检查历史容量")
async def history_plan(doc_id: str, kind: str = Query(default="snapshot")):
    if kind not in {"snapshot", "inverse", "undo", "redo"}:
        raise PdfError(422, "历史记录类型无效", code="INVALID_HISTORY_MODE")
    session = session_manager.get(doc_id)
    with session.lock:
        if session.pending_job_id is not None:
            raise PdfError(409, "文档修改任务仍在执行", code="SESSION_BUSY")
        if kind in {"undo", "redo"}:
            return job_manager.history_transition_plan(session, kind)
        return job_manager.history_plan(session, kind)


async def _submit_history_action(
    doc_id: str,
    payload: HistoryActionRequest,
    action: str,
    idempotency_key: str,
    history_policy: str,
):
    session = session_manager.get(doc_id)
    request_hash = job_manager.fingerprint(
        f"editor_history_{action}",
        {
            "docId": doc_id,
            "expectedRevision": payload.expectedRevision,
            "historyPolicy": history_policy or None,
        },
        [],
    )
    existing = job_manager.find_existing(
        idempotency_key, request_hash, session_id=doc_id
    )
    if existing is not None:
        return JSONResponse(status_code=202, content={"jobId": existing.id, "state": existing.state})

    with session.lock:
        _ensure_session_writable(session, payload.expectedRevision)
        source_stack = session.undo_stack if action == "undo" else session.redo_stack
        if not source_stack:
            raise PdfError(409, "没有可用的撤销/重做记录", code="HISTORY_EMPTY")
        entry = source_stack[-1]
        if entry.kind == "snapshot":
            if entry.snapshot_path is None or not entry.snapshot_path.is_file():
                raise PdfError(409, "历史快照已丢失", code="HISTORY_SNAPSHOT_MISSING")
            operation = "editor_history_restore"
            job_payload = {
                "source_path": str(entry.snapshot_path),
                "page_ids": list(entry.page_ids or []),
            }
        else:
            inverse = entry.undo_action if action == "undo" else entry.redo_action
            if not inverse:
                raise PdfError(409, "历史操作记录无效", code="HISTORY_ENTRY_INVALID")
            page_id = inverse.get("pageId")
            try:
                current_page = session.page_ids.index(page_id)
            except ValueError as exc:
                raise PdfError(409, "历史页面已变化", code="HISTORY_CONFLICT") from exc
            if inverse["kind"] == "rotate":
                operation = "editor_rotate"
                job_payload = {
                    "source_path": str(session.path),
                    "page_ids": list(session.page_ids),
                    "page": current_page,
                    "angle": int(inverse["angle"]),
                }
            elif inverse["kind"] == "move":
                operation = "editor_move"
                job_payload = {
                    "source_path": str(session.path),
                    "page_ids": list(session.page_ids),
                    "from": current_page,
                    "to": int(inverse["targetIndex"]),
                }
            else:
                raise PdfError(409, "历史操作类型不受支持", code="HISTORY_ENTRY_INVALID")

    job = await job_manager.submit(
        operation,
        job_payload,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        session_id=doc_id,
        expected_revision=payload.expectedRevision,
        history_action=action,
        history_entry=entry,
        history_policy=history_policy or None,
    )
    return JSONResponse(status_code=202, content={"jobId": job.id, "state": job.state})


@router.post("/{doc_id}/history/undo", summary="撤销")
async def undo(
    doc_id: str,
    payload: HistoryActionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    history_policy: str = Header(default="", alias="History-Policy"),
):
    return await _submit_history_action(doc_id, payload, "undo", idempotency_key, history_policy)


@router.post("/{doc_id}/history/redo", summary="重做")
async def redo(
    doc_id: str,
    payload: HistoryActionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    history_policy: str = Header(default="", alias="History-Policy"),
):
    return await _submit_history_action(doc_id, payload, "redo", idempotency_key, history_policy)


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
    history_policy: str = Header(default="", alias="History-Policy"),
):
    session = session_manager.get(doc_id)
    payload_dict = payload.model_dump()
    payload_dict.update({"source_path": str(session.path), "page_ids": list(session.page_ids)})
    request_hash = job_manager.fingerprint(
        "editor_apply",
        {**payload_dict, "docId": doc_id, "historyPolicy": history_policy or None},
        [],
    )
    existing = job_manager.find_existing(
        idempotency_key, request_hash, session_id=doc_id
    )
    if existing is not None:
        return _accepted(existing)
    await job_manager.run_internal(
        "editor_preflight_edits",
        {
            "source_path": str(session.path),
            "page_ids": list(session.page_ids),
            "edits": payload_dict["edits"],
        },
        session_id=doc_id,
        expected_revision=payload.expectedRevision,
    )
    job = await job_manager.submit(
        "editor_apply",
        payload_dict,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        session_id=doc_id,
        expected_revision=payload.expectedRevision,
        history_policy=history_policy or None,
    )
    return _accepted(job)


@router.post("/{doc_id}/text/add", summary="添加文字")
async def add_text(
    doc_id: str,
    payload: AddTextRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    history_policy: str = Header(default="", alias="History-Policy"),
):
    session = session_manager.get(doc_id)
    payload_dict = payload.model_dump()
    payload_dict.update({"source_path": str(session.path), "page_ids": list(session.page_ids)})
    request_hash = job_manager.fingerprint(
        "editor_add_text",
        {**payload_dict, "docId": doc_id, "historyPolicy": history_policy or None},
        [],
    )
    existing = job_manager.find_existing(
        idempotency_key, request_hash, session_id=doc_id
    )
    if existing is not None:
        return _accepted(existing)
    await job_manager.run_internal(
        "editor_preflight_page_write",
        {
            "source_path": str(session.path),
            "page_ids": list(session.page_ids),
            "page": payload.page,
            "page_id": payload.pageId,
            "rect": [payload.x, payload.y, payload.x + 1, payload.y + 1],
        },
        session_id=doc_id,
        expected_revision=payload.expectedRevision,
    )
    job = await job_manager.submit(
        "editor_add_text",
        payload_dict,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        session_id=doc_id,
        expected_revision=payload.expectedRevision,
        history_policy=history_policy or None,
    )
    return _accepted(job)


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
    history_policy: str = Header(default="", alias="History-Policy"),
):
    if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
        raise PdfError(400, "图片尺寸必须为正数")
    session = session_manager.get(doc_id)
    img_path = await save_upload(image, suffix=Path(image.filename or "stamp.png").suffix or ".png")
    try:
        validate_image(img_path)
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
            "editor_add_image",
            {
                **payload,
                "docId": doc_id,
                "expectedRevision": expectedRevision,
                "historyPolicy": history_policy or None,
            },
            [temp_manager.get_digest(img_path)],
        )
        existing = job_manager.find_existing(
            idempotency_key, request_hash, session_id=doc_id
        )
        if existing is not None:
            temp_manager.discard(img_path)
            return _accepted(existing)
        await job_manager.run_internal(
            "editor_preflight_page_write",
            {
                "source_path": str(session.path),
                "page_ids": list(session.page_ids),
                "page": page,
                "page_id": pageId,
                "rect": [x, y, x + w, y + h],
            },
            session_id=doc_id,
            expected_revision=expectedRevision,
        )
        job = await job_manager.submit(
            "editor_add_image",
            payload,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            input_paths=[img_path],
            session_id=doc_id,
            expected_revision=expectedRevision,
            history_policy=history_policy or None,
        )
        return _accepted(job)
    except BaseException:
        temp_manager.discard(img_path)
        raise


@router.post("/{doc_id}/pages/rotate", summary="旋转页面")
async def rotate_page(
    doc_id: str,
    payload: RotatePageRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    history_policy: str = Header(default="", alias="History-Policy"),
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
        history_policy=history_policy or None,
    )
    return JSONResponse(status_code=202, content={"jobId": job.id, "state": job.state})


@router.post("/{doc_id}/pages/delete", summary="删除页面")
async def delete_pages(
    doc_id: str,
    payload: DeletePagesRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    history_policy: str = Header(default="", alias="History-Policy"),
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
        history_policy=history_policy or None,
    )
    return JSONResponse(status_code=202, content={"jobId": job.id, "state": job.state})


@router.post("/{doc_id}/pages/move", summary="移动页面")
async def move_page(
    doc_id: str,
    payload: MovePageRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    history_policy: str = Header(default="", alias="History-Policy"),
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
        history_policy=history_policy or None,
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
            if session.active_text_reads:
                raise PdfError(409, "单页文本读取仍在执行，请稍后关闭", code="SESSION_BUSY")
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
