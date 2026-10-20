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
from fastapi import APIRouter, File, Form, Query, UploadFile

from ..core import pdf_engine
from ..core.errors import PdfError
from ..core.input_validation import validate_image
from ..core.responses import file_response
from ..core.tempfile import save_upload, temp_manager
from ..services.sessions import session_manager
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


def extract_text_model(
    doc: fitz.Document, page_ids: list[str] | None = None
) -> list[dict]:
    """Extract line-level editable units per page."""
    pages: list[dict] = []
    for pno in range(doc.page_count):
        page = doc[pno]
        page_id = page_ids[pno] if page_ids and pno < len(page_ids) else f"page-{pno}"
        lines: list[dict] = []
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue  # image block
            for line in block["lines"]:
                spans = [s for s in line["spans"] if s.get("text")]
                if not spans:
                    continue
                text = "".join(s["text"] for s in spans)
                if not text.strip():
                    continue
                bbox = line["bbox"]
                first = spans[0]
                lines.append(
                    {
                        "id": f"{page_id}:l{len(lines)}",
                        "pageId": page_id,
                        "originalHash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        "bbox": [round(v, 2) for v in bbox],
                        "fontSize": round(float(first.get("size", 12)), 1),
                        "font": _map_font(str(first.get("font", "")), text),
                        "color": _line_color(int(first.get("color", 0))),
                        "text": text,
                    }
                )
        pages.append(
            {
                "page": pno,
                "pageId": page_id,
                "width": round(page.rect.width, 2),
                "height": round(page.rect.height, 2),
                "rotation": page.rotation,
                "lines": lines,
            }
        )
    return pages


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
    page_model = extract_text_model(doc, session.page_ids)[edit.page]
    line = next((item for item in page_model["lines"] if item["id"] == edit.lineId), None)
    if (
        line is None
        or line["originalHash"] != edit.originalHash
        or line["text"] != edit.original
        or any(abs(float(a) - float(b)) > 0.5 for a, b in zip(line["bbox"], edit.bbox))
    ):
        raise PdfError(409, "文本行已变化，请重新定位草稿", code="REVISION_CONFLICT")


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------

@router.post("/open", summary="打开 PDF 进入编辑会话")
async def open_document(fileInput: UploadFile = File(...)):
    src_path = await save_upload(fileInput)
    session = None
    try:
        doc = pdf_engine.open_pdf(src_path)
        try:
            page_count = doc.page_count
        finally:
            doc.close()
        session = session_manager.create(
            src_path, fileInput.filename or "document.pdf", page_count
        )
        doc = pdf_engine.open_pdf(session.path)
        try:
            model = extract_text_model(doc, session.page_ids)
            meta = _page_meta(doc, session.page_ids)
        finally:
            doc.close()
    except BaseException:
        if session is not None:
            session_manager.close(session.id)
        raise
    finally:
        temp_manager.discard(src_path)
    return {
        "docId": session.id,
        "originalName": session.original_name,
        "pageCount": len(meta),
        "revision": session.revision,
        "pages": meta,
        "textModel": model,
    }


@router.post("/{doc_id}/apply", summary="应用文本编辑（空文本=涂抹）")
async def apply_edits(doc_id: str, payload: ApplyEditsRequest):
    edits = payload.edits
    session = session_manager.get(doc_id)
    with session.lock:
        _check_revision(session, payload.expectedRevision)
        doc = pdf_engine.open_pdf(session.path)
        changed = False
        try:
            for edit in edits:
                if edit.kind == "text":
                    _check_line_anchor(session, doc, edit)
                else:
                    page = _check_page(session, edit.page, edit.pageId or "", doc)
                page = doc[edit.page]
                bbox = fitz.Rect(edit.bbox)
                if not page.rect.contains(bbox):
                    raise PdfError(422, "编辑区域超出页面范围", code="INVALID_BBOX")
                new_text = edit.text
                is_whiteout = edit.kind == "cover" or not new_text.strip()
                if edit.kind == "text" and not is_whiteout and new_text == edit.original:
                    continue
                page.add_redact_annot(bbox, fill=(1, 1, 1))
                page.apply_redactions(
                    images=fitz.PDF_REDACT_IMAGE_NONE,
                    graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                )
                if is_whiteout:
                    page.draw_rect(bbox, color=None, fill=(1, 1, 1))
                    changed = True
                    continue
                font = pdf_engine.pick_font(new_text)
                size = float(edit.fontSize or max(bbox.height * 0.8, 6))
                inserted = False
                while size >= 6:
                    rc = page.insert_textbox(
                        bbox,
                        new_text,
                        fontname=font,
                        fontsize=size,
                        align=fitz.TEXT_ALIGN_LEFT,
                    )
                    if rc >= 0:
                        inserted = True
                        break
                    size *= 0.95
                if not inserted:
                    page.insert_text(
                        (bbox.x0 + 1, bbox.y1 - 1),
                        new_text,
                        fontname=font,
                        fontsize=6,
                    )
                changed = True
            if changed:
                doc = pdf_engine.commit_and_reload(doc, session.path, garbage=4, deflate=True)
                session.revision += 1
            model = extract_text_model(doc, session.page_ids)
            meta = _page_meta(doc, session.page_ids)
        finally:
            doc.close()
        return {
            "ok": True,
            "pageCount": len(meta),
            "revision": session.revision,
            "pages": meta,
            "textModel": model,
        }


@router.post("/{doc_id}/text/add", summary="添加文字")
async def add_text(doc_id: str, payload: AddTextRequest):
    color = _parse_color(payload.color)
    font = pdf_engine.pick_font(payload.text)
    session = session_manager.get(doc_id)
    with session.lock:
        _check_revision(session, payload.expectedRevision)
        doc = pdf_engine.open_pdf(session.path)
        try:
            page = _check_page(session, payload.page, payload.pageId, doc)
            page.insert_text(
                (payload.x, payload.y), payload.text, fontname=font,
                fontsize=payload.fontSize, color=color
            )
            doc = pdf_engine.commit_and_reload(doc, session.path, garbage=4, deflate=True)
            session.revision += 1
            model = extract_text_model(doc, session.page_ids)
            meta = _page_meta(doc, session.page_ids)
        finally:
            doc.close()
        return {
            "ok": True,
            "pageCount": len(meta),
            "revision": session.revision,
            "pages": meta,
            "textModel": model,
        }


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
):
    if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
        raise PdfError(400, "图片尺寸必须为正数")
    session = session_manager.get(doc_id)
    img_path = await save_upload(image, suffix=Path(image.filename or "stamp.png").suffix or ".png")
    try:
        validate_image(img_path)
        with session.lock:
            _check_revision(session, expectedRevision)
            doc = pdf_engine.open_pdf(session.path)
            try:
                _check_page(session, page, pageId, doc)
                rect = fitz.Rect(x, y, x + w, y + h)
                if not doc[page].rect.contains(rect):
                    raise PdfError(422, "图片区域超出页面范围", code="INVALID_BBOX")
                try:
                    doc[page].insert_image(rect, filename=str(img_path))
                except Exception as exc:  # noqa: BLE001
                    raise PdfError(422, "图片无法插入 PDF", code="INVALID_IMAGE") from exc
                doc = pdf_engine.commit_and_reload(doc, session.path, garbage=4, deflate=True)
                session.revision += 1
                model = extract_text_model(doc, session.page_ids)
                meta = _page_meta(doc, session.page_ids)
            finally:
                doc.close()
    finally:
        temp_manager.discard(img_path)
    return {
        "ok": True,
        "pageCount": len(meta),
        "revision": session.revision,
        "pages": meta,
        "textModel": model,
    }


@router.post("/{doc_id}/pages/rotate", summary="旋转页面")
async def rotate_page(doc_id: str, payload: RotatePageRequest):
    page_no, angle = payload.page, payload.angle
    if angle not in (90, 180, 270):
        raise PdfError(400, "旋转角度仅支持 90/180/270")
    session = session_manager.get(doc_id)
    with session.lock:
        _check_revision(session, payload.expectedRevision)
        doc = pdf_engine.open_pdf(session.path)
        try:
            if page_no < 0 or page_no >= doc.page_count:
                raise PdfError(400, f"页码越界: {page_no}")
            doc[page_no].set_rotation((doc[page_no].rotation + angle) % 360)
            doc = pdf_engine.commit_and_reload(doc, session.path, garbage=4, deflate=True)
            session.revision += 1
            model = extract_text_model(doc, session.page_ids)
            meta = _page_meta(doc, session.page_ids)
        finally:
            doc.close()
        return {"ok": True, "pageCount": len(meta), "revision": session.revision, "pages": meta, "textModel": model}


@router.post("/{doc_id}/pages/delete", summary="删除页面")
async def delete_pages(doc_id: str, payload: DeletePagesRequest):
    pages = payload.pages
    session = session_manager.get(doc_id)
    with session.lock:
        _check_revision(session, payload.expectedRevision)
        doc = pdf_engine.open_pdf(session.path)
        try:
            indices = sorted(set(pages))
            if any(i < 0 or i >= doc.page_count for i in indices):
                raise PdfError(400, "页码越界")
            if len(indices) >= doc.page_count:
                raise PdfError(400, "不能删除全部页面")
            doc.delete_pages(indices)
            doc = pdf_engine.commit_and_reload(doc, session.path, garbage=4, deflate=True)
            session.page_ids = [pid for i, pid in enumerate(session.page_ids) if i not in set(indices)]
            session.revision += 1
            model = extract_text_model(doc, session.page_ids)
            meta = _page_meta(doc, session.page_ids)
        finally:
            doc.close()
        return {"ok": True, "pageCount": len(meta), "revision": session.revision, "pages": meta, "textModel": model}


@router.post("/{doc_id}/pages/move", summary="移动页面")
async def move_page(doc_id: str, payload: MovePageRequest):
    src, dst = payload.fromPage, payload.toPage
    session = session_manager.get(doc_id)
    with session.lock:
        _check_revision(session, payload.expectedRevision)
        doc = pdf_engine.open_pdf(session.path)
        try:
            total = doc.page_count
            if not (0 <= src < total) or not (0 <= dst < total):
                raise PdfError(400, "页码越界")
            order = list(range(total))
            order.pop(src)
            order.insert(dst, src)
            doc.select(order)
            doc = pdf_engine.commit_and_reload(doc, session.path, garbage=4, deflate=True)
            ids = list(session.page_ids)
            moved_id = ids.pop(src)
            ids.insert(dst, moved_id)
            session.page_ids = ids
            session.revision += 1
            model = extract_text_model(doc, session.page_ids)
            meta = _page_meta(doc, session.page_ids)
        finally:
            doc.close()
        return {"ok": True, "pageCount": len(meta), "revision": session.revision, "pages": meta, "textModel": model}


@router.get("/{doc_id}/download", summary="另存为下载")
async def download(doc_id: str, filename: str = Query(default="")):
    session = session_manager.get(doc_id)
    name = filename.strip() or session.original_name
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    # Session files are owned by the session lifecycle (close / TTL),
    # never deleted after a single response.
    return file_response(session.path, name, delete_after=False)


@router.post("/{doc_id}/close", summary="关闭编辑会话")
async def close_session(doc_id: str):
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
