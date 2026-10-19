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

from pathlib import Path

import fitz
from fastapi import APIRouter, File, Form, Query, UploadFile

from ..core import pdf_engine
from ..core.errors import PdfError
from ..core.responses import file_response
from ..core.tempfile import save_upload, temp_manager
from ..services.sessions import session_manager

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


def extract_text_model(doc: fitz.Document) -> list[dict]:
    """Extract line-level editable units per page."""
    pages: list[dict] = []
    for pno in range(doc.page_count):
        page = doc[pno]
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
                        "id": f"p{pno}-l{len(lines)}",
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
                "width": round(page.rect.width, 2),
                "height": round(page.rect.height, 2),
                "rotation": page.rotation,
                "lines": lines,
            }
        )
    return pages


def _page_meta(doc: fitz.Document) -> list[dict]:
    return [
        {
            "page": i,
            "width": round(doc[i].rect.width, 2),
            "height": round(doc[i].rect.height, 2),
            "rotation": doc[i].rotation,
        }
        for i in range(doc.page_count)
    ]


def _load_session_pdf(doc_id: str) -> fitz.Document:
    session = session_manager.get(doc_id)
    return pdf_engine.open_pdf(session.path)


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------

@router.post("/open", summary="打开 PDF 进入编辑会话")
async def open_document(fileInput: UploadFile = File(...)):
    src_path = await save_upload(fileInput)
    doc = pdf_engine.open_pdf(src_path)
    try:
        model = extract_text_model(doc)
        meta = _page_meta(doc)
    finally:
        doc.close()
    session = session_manager.create(src_path, fileInput.filename or "document.pdf")
    temp_manager.discard(src_path)
    return {
        "docId": session.id,
        "originalName": session.original_name,
        "pageCount": len(meta),
        "pages": meta,
        "textModel": model,
    }


@router.post("/{doc_id}/apply", summary="应用文本编辑（空文本=涂抹）")
async def apply_edits(doc_id: str, payload: dict):
    edits = payload.get("edits") or []
    if not isinstance(edits, list):
        raise PdfError(400, "edits 必须是数组")
    session = session_manager.get(doc_id)
    doc = pdf_engine.open_pdf(session.path)
    try:
        for edit in edits:
            try:
                page_no = int(edit["page"])
                bbox = fitz.Rect(edit["bbox"])
                new_text = str(edit.get("text", ""))
            except (KeyError, TypeError, ValueError) as exc:
                raise PdfError(400, f"无效的编辑项: {exc}") from exc
            if page_no < 0 or page_no >= doc.page_count:
                raise PdfError(400, f"页码越界: {page_no}")
            page = doc[page_no]
            original = str(edit.get("original", ""))
            is_whiteout = not new_text.strip()
            if not is_whiteout and new_text == original:
                continue
            # 1. wipe the original line (keep images / line art untouched)
            page.add_redact_annot(bbox, fill=(1, 1, 1))
            page.apply_redactions(
                images=fitz.PDF_REDACT_IMAGE_NONE,
                graphics=fitz.PDF_REDACT_LINE_ART_NONE,
            )
            if is_whiteout:
                # paint solid white on top so images/graphics are covered too
                page.draw_rect(bbox, color=None, fill=(1, 1, 1))
                continue
            # 2. reinsert with auto font + auto-shrinking size
            font = pdf_engine.pick_font(new_text)
            size = float(edit.get("fontSize") or max(bbox.height * 0.8, 6))
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
        doc = pdf_engine.commit_and_reload(doc, session.path, garbage=4, deflate=True)
        model = extract_text_model(doc)
        meta = _page_meta(doc)
    finally:
        doc.close()
    return {"ok": True, "pageCount": len(meta), "pages": meta, "textModel": model}


@router.post("/{doc_id}/text/add", summary="添加文字")
async def add_text(doc_id: str, payload: dict):
    try:
        page_no = int(payload["page"])
        x, y = float(payload["x"]), float(payload["y"])
        text = str(payload["text"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PdfError(400, f"无效的参数: {exc}") from exc
    if not text:
        raise PdfError(400, "文字不能为空")
    font_size = float(payload.get("fontSize", 12))
    if not (4 <= font_size <= 200):
        raise PdfError(400, "字号需在 4-200 之间")
    color = _parse_color(str(payload.get("color", "#000000")))
    font = pdf_engine.pick_font(text)

    session = session_manager.get(doc_id)
    doc = pdf_engine.open_pdf(session.path)
    try:
        if page_no < 0 or page_no >= doc.page_count:
            raise PdfError(400, f"页码越界: {page_no}")
        doc[page_no].insert_text((x, y), text, fontname=font, fontsize=font_size, color=color)
        doc = pdf_engine.commit_and_reload(doc, session.path, garbage=4, deflate=True)
        model = extract_text_model(doc)
        meta = _page_meta(doc)
    finally:
        doc.close()
    return {"ok": True, "pageCount": len(meta), "pages": meta, "textModel": model}


@router.post("/{doc_id}/image/add", summary="添加图片/印章")
async def add_image(
    doc_id: str,
    image: UploadFile = File(...),
    page: int = Form(...),
    x: float = Form(...),
    y: float = Form(...),
    w: float = Form(...),
    h: float = Form(...),
):
    if w <= 0 or h <= 0:
        raise PdfError(400, "图片尺寸必须为正数")
    session = session_manager.get(doc_id)
    img_path = await save_upload(image, suffix=Path(image.filename or "stamp.png").suffix or ".png")
    try:
        doc = pdf_engine.open_pdf(session.path)
        try:
            if page < 0 or page >= doc.page_count:
                raise PdfError(400, f"页码越界: {page}")
            rect = fitz.Rect(x, y, x + w, y + h)
            doc[page].insert_image(rect, filename=str(img_path))
            doc = pdf_engine.commit_and_reload(doc, session.path, garbage=4, deflate=True)
            model = extract_text_model(doc)
            meta = _page_meta(doc)
        finally:
            doc.close()
    finally:
        temp_manager.discard(img_path)
    return {"ok": True, "pageCount": len(meta), "pages": meta, "textModel": model}


@router.post("/{doc_id}/pages/rotate", summary="旋转页面")
async def rotate_page(doc_id: str, payload: dict):
    try:
        page_no = int(payload["page"])
        angle = int(payload["angle"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PdfError(400, f"无效的参数: {exc}") from exc
    if angle not in (90, 180, 270):
        raise PdfError(400, "旋转角度仅支持 90/180/270")
    session = session_manager.get(doc_id)
    doc = pdf_engine.open_pdf(session.path)
    try:
        if page_no < 0 or page_no >= doc.page_count:
            raise PdfError(400, f"页码越界: {page_no}")
        doc[page_no].set_rotation((doc[page_no].rotation + angle) % 360)
        doc = pdf_engine.commit_and_reload(doc, session.path, garbage=4, deflate=True)
        model = extract_text_model(doc)
        meta = _page_meta(doc)
    finally:
        doc.close()
    return {"ok": True, "pageCount": len(meta), "pages": meta, "textModel": model}


@router.post("/{doc_id}/pages/delete", summary="删除页面")
async def delete_pages(doc_id: str, payload: dict):
    pages = payload.get("pages")
    if not isinstance(pages, list) or not pages:
        raise PdfError(400, "pages 必须是非空数组")
    session = session_manager.get(doc_id)
    doc = pdf_engine.open_pdf(session.path)
    try:
        try:
            indices = sorted({int(p) for p in pages})
        except (TypeError, ValueError) as exc:
            raise PdfError(400, f"无效的页码: {exc}") from exc
        if any(i < 0 or i >= doc.page_count for i in indices):
            raise PdfError(400, "页码越界")
        if len(indices) >= doc.page_count:
            raise PdfError(400, "不能删除全部页面")
        doc.delete_pages(indices)
        doc = pdf_engine.commit_and_reload(doc, session.path, garbage=4, deflate=True)
        model = extract_text_model(doc)
        meta = _page_meta(doc)
    finally:
        doc.close()
    return {"ok": True, "pageCount": len(meta), "pages": meta, "textModel": model}


@router.post("/{doc_id}/pages/move", summary="移动页面")
async def move_page(doc_id: str, payload: dict):
    try:
        src = int(payload["from"])
        dst = int(payload["to"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PdfError(400, f"无效的参数: {exc}") from exc
    session = session_manager.get(doc_id)
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
        model = extract_text_model(doc)
        meta = _page_meta(doc)
    finally:
        doc.close()
    return {"ok": True, "pageCount": len(meta), "pages": meta, "textModel": model}


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
