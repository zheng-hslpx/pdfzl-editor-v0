"""Spawn-safe PDF operation worker.

Only paths and JSON-compatible arguments cross the process boundary. Every
PyMuPDF Document is opened, used, and closed inside this process.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import shutil
import zipfile
from pathlib import Path
from typing import Any

import fitz
from PIL import Image

from .. import config
from ..core import pdf_engine
from ..core.errors import PdfError
from ..core.input_validation import validate_image, validate_pdf

logger = logging.getLogger("pdf_editor.worker")


def _progress(queue, phase: str, completed: int | None = None,
              total: int | None = None, unit: str | None = None) -> None:
    try:
        queue.put_nowait({
            "phase": phase,
            "completed": completed,
            "total": total,
            "unit": unit,
        })
    except Exception:
        pass


def _check_cancel(cancel_event) -> None:
    if cancel_event.is_set():
        raise PdfError(499, "任务已取消", code="CANCELLED")


def _name(value: str | None, fallback: str = "document") -> str:
    name = Path(value or fallback).name
    return Path(name).stem or fallback


def _read_pdf(path: str) -> fitz.Document:
    return pdf_engine.open_pdf(Path(path))


def _display_to_page_point(page: fitz.Page, point: fitz.Point) -> fitz.Point:
    """Map a point from the rotated page view back to PyMuPDF page space."""
    return point * page.derotation_matrix


def _display_textbox_to_page(page: fitz.Page, rect: fitz.Rect) -> fitz.Rect:
    """Map its center but preserve display width/height for horizontal layout."""
    display_center = fitz.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
    center = _display_to_page_point(page, display_center)
    half_width = rect.width / 2
    half_height = rect.height / 2
    return fitz.Rect(
        center.x - half_width,
        center.y - half_height,
        center.x + half_width,
        center.y + half_height,
    )


def _image_dpi(path: Path) -> tuple[float, float]:
    """Return embedded image DPI, with the common 96 DPI fallback."""
    try:
        with Image.open(path) as image:
            raw = image.info.get("dpi")
        if isinstance(raw, (tuple, list)) and len(raw) >= 2:
            x_dpi, y_dpi = float(raw[0]), float(raw[1])
            if math.isfinite(x_dpi) and math.isfinite(y_dpi) and x_dpi > 0 and y_dpi > 0:
                return x_dpi, y_dpi
    except (OSError, TypeError, ValueError):
        pass
    return 96.0, 96.0


def _encryption_status(document: fitz.Document) -> bool | None:
    try:
        return bool(document.is_encrypted)
    except Exception:
        return None


def _unknown_pdf_info(path: Path) -> dict[str, Any]:
    return {
        "kind": "json",
        "data": {
            "pageCount": None,
            "pageSize": None,
            "pdfVersion": None,
            "metadata": None,
            "isEncrypted": None,
            "fonts": [],
            "tocEntries": None,
            "fileSize": path.stat().st_size,
        },
    }


def _validate_anchor(
    doc: fitz.Document,
    page_ids: list[str],
    edit: dict,
    page_text_models: dict[int, dict] | None = None,
) -> fitz.Page:
    page_no = int(edit["page"])
    if not (0 <= page_no < doc.page_count) or page_no >= len(page_ids):
        raise PdfError(409, "页面身份已变化，请重新定位草稿", code="REVISION_CONFLICT")
    if page_ids[page_no] != edit.get("pageId"):
        raise PdfError(409, "页面身份已变化，请重新定位草稿", code="REVISION_CONFLICT")
    page = doc[page_no]
    if page.rotation != 0:
        raise PdfError(
            422,
            "旋转页暂不支持坐标编辑，请先用旋转工具归一化",
            code="ROTATED_PAGE_UNSUPPORTED",
        )
    if edit.get("kind") == "cover":
        return page

    from ..routers.edit import extract_page_text_model

    model = page_text_models.get(page_no) if page_text_models is not None else None
    if model is None:
        model = extract_page_text_model(doc, page_no, page_ids[page_no])
        if page_text_models is not None:
            page_text_models[page_no] = model
    line = next((row for row in model["lines"] if row["id"] == edit.get("lineId")), None)
    bbox = edit.get("bbox") or []
    if (
        line is None
        or line["originalHash"] != edit.get("originalHash")
        or line["text"] != edit.get("original", "")
        or len(bbox) != 4
        or any(abs(float(a) - float(b)) > 0.5 for a, b in zip(line["bbox"], bbox))
    ):
        raise PdfError(409, "文本行已变化，请重新定位草稿", code="REVISION_CONFLICT")
    return page


def _prepare_editor_batch(
    doc: fitz.Document,
    page_ids: list[str],
    edits: list[dict],
) -> list[dict[str, Any]]:
    page_text_models: dict[int, dict] = {}
    prepared: list[dict[str, Any]] = []
    for edit in edits:
        page = _validate_anchor(doc, page_ids, edit, page_text_models)
        bbox = fitz.Rect(edit["bbox"])
        if not page.rect.contains(bbox):
            raise PdfError(422, "编辑区域超出页面范围", code="INVALID_BBOX")
        text = str(edit.get("text", ""))
        whiteout = edit.get("kind") == "cover" or not text.strip()
        prepared.append({
            "edit": edit,
            "page_no": int(edit["page"]),
            "bbox": bbox,
            "text": text,
            "whiteout": whiteout,
            "no_op": edit.get("kind") == "text" and not whiteout and text == edit.get("original"),
        })

    active = [item for item in prepared if not item["no_op"]]
    for index, first in enumerate(active):
        a = first["bbox"]
        for second in active[index + 1:]:
            if first["page_no"] != second["page_no"]:
                continue
            b = second["bbox"]
            overlap_width = min(a.x1, b.x1) - max(a.x0, b.x0)
            overlap_height = min(a.y1, b.y1) - max(a.y0, b.y0)
            if overlap_width > 0 and overlap_height > 0:
                raise PdfError(
                    422,
                    "同一页的编辑区域重叠，请调整范围后重试",
                    code="OVERLAPPING_EDITS",
                )
    return prepared


def _save_document(doc: fitz.Document, output: Path, **options) -> None:
    doc.save(str(output), **options)
    doc.close()
    check = pdf_engine.open_pdf(output)
    check.close()


def _page_metadata(doc: fitz.Document, page_ids: list[str]) -> list[dict]:
    return [
        {
            "page": index,
            "pageId": page_ids[index] if index < len(page_ids) else f"page-{index}",
            "width": round(doc[index].rect.width, 2),
            "height": round(doc[index].rect.height, 2),
            "rotation": doc[index].rotation,
        }
        for index in range(doc.page_count)
    ]


def _parse_segments(spec: str, total: int) -> list[tuple[list[int], str]]:
    spec = (spec or "").strip()
    if not spec:
        raise PdfError(400, "拆分范围不能为空")
    if spec.lower() == "all":
        return [(list(range(total)), "all")]
    result = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if part.lower() == "all":
            raise PdfError(400, "all 只能单独使用")
        try:
            if "-" in part:
                start, end = (int(value) for value in part.split("-", 1))
            else:
                start = end = int(part)
        except ValueError as exc:
            raise PdfError(400, f"无效的拆分范围: {part}") from exc
        if start < 1 or end > total or start > end:
            raise PdfError(400, f"拆分范围越界或无效: {part}")
        pages = list(range(start - 1, end))
        result.append((pages, str(start) if start == end else f"{start}-{end}"))
    if not result:
        raise PdfError(400, "拆分范围解析结果为空")
    return result


def _parse_color(value: str) -> tuple[float, float, float]:
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) != 6:
        raise PdfError(400, "颜色值无效")
    try:
        return tuple(int(text[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError as exc:
        raise PdfError(400, "颜色值无效") from exc


def _save_result(value: dict[str, Any], result_path: Path) -> None:
    temporary = result_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, result_path)


def _run_operation(operation: str, payload: dict[str, Any], work_dir: Path,
                   progress_queue, cancel_event) -> dict[str, Any]:
    """Run one operation and return a small JSON-safe result descriptor."""
    work_dir.mkdir(parents=True, exist_ok=True)
    output = work_dir / "result"
    if operation == "test_sleep":
        duration = max(0.0, float(payload.get("seconds", 0)))
        interval = 0.05
        steps = max(1, math.ceil(duration / interval))
        for step in range(steps):
            _check_cancel(cancel_event)
            import time as _time
            _time.sleep(min(interval, max(0.0, duration - step * interval)))
            _progress(progress_queue, "processing", step + 1, steps, "steps")
        return {"kind": "json", "data": {"slept": duration}}
    if operation == "test_allocate":
        amount = max(0, int(payload.get("bytes", 0)))
        buffer = bytearray(amount)
        for offset in range(0, amount, 1024 * 1024):
            buffer[offset] = 1
        return {"kind": "json", "data": {"allocated": len(buffer)}}
    if operation == "validate_pdf":
        page_count = validate_pdf(
            Path(payload["source_path"]),
            allow_encrypted=bool(payload.get("allow_encrypted", False)),
        )
        return {"kind": "json", "data": {"pageCount": page_count}}
    if operation == "test_session_mutation":
        duration = max(0.0, float(payload.get("seconds", 0)))
        deadline = __import__("time").monotonic() + duration
        while __import__("time").monotonic() < deadline:
            _check_cancel(cancel_event)
            __import__("time").sleep(0.02)
        destination = output.with_suffix(".pdf")
        shutil.copy2(payload["source_path"], destination)
        return {
            "kind": "session_mutation",
            "artifact": destination.name,
            "data": {"pageIds": payload["page_ids"]},
        }
    if operation == "editor_open":
        doc = _read_pdf(payload["source_path"])
        try:
            page_ids = payload["page_ids"]
            page_metadata = _page_metadata(doc, page_ids)
            return {
                "kind": "json",
                "data": {
                    "docId": payload["docId"],
                    "originalName": payload["originalName"],
                    "pageCount": doc.page_count,
                    "revision": payload.get("revision", 0),
                    "pages": page_metadata[:50],
                    "_allPageMetadata": page_metadata,
                },
            }
        finally:
            doc.close()

    if operation == "editor_page_text":
        doc = _read_pdf(payload["source_path"])
        try:
            page_no = int(payload["page"])
            if not 0 <= page_no < doc.page_count:
                raise PdfError(404, "页面不存在")
            from ..routers.edit import extract_page_text_model

            page = extract_page_text_model(doc, page_no, payload["page_id"])
            return {"kind": "json", "data": {"page": page}}
        finally:
            doc.close()

    if operation == "editor_preflight_edits":
        doc = _read_pdf(payload["source_path"])
        try:
            page_text_models: dict[int, dict] = {}
            for edit in payload["edits"]:
                page = _validate_anchor(
                    doc, payload["page_ids"], edit, page_text_models
                )
                if not page.rect.contains(fitz.Rect(edit["bbox"])):
                    raise PdfError(422, "编辑区域超出页面范围", code="INVALID_BBOX")
            return {"kind": "json", "data": {"ok": True}}
        finally:
            doc.close()

    if operation == "editor_preflight_page_write":
        doc = _read_pdf(payload["source_path"])
        try:
            page_no = int(payload["page"])
            page_ids = payload["page_ids"]
            if page_no < 0 or page_no >= doc.page_count:
                raise PdfError(400, f"页码越界: {page_no}")
            if page_no >= len(page_ids) or page_ids[page_no] != payload["page_id"]:
                raise PdfError(409, "页面身份已变化，请重新定位操作", code="REVISION_CONFLICT")
            page = doc[page_no]
            if page.rotation != 0:
                raise PdfError(
                    422,
                    "旋转页暂不支持坐标编辑，请先用旋转工具归一化",
                    code="ROTATED_PAGE_UNSUPPORTED",
                )
            rect = payload.get("rect")
            if rect is not None and not page.rect.contains(fitz.Rect(rect)):
                raise PdfError(422, "编辑区域超出页面范围", code="INVALID_BBOX")
            return {"kind": "json", "data": {"ok": True}}
        finally:
            doc.close()

    if operation == "editor_history_restore":
        source = Path(payload["source_path"])
        doc = _read_pdf(str(source))
        page_ids = list(payload["page_ids"])
        try:
            _check_cancel(cancel_event)
            data = {
                "pageCount": doc.page_count,
                "pageIds": page_ids,
                "pages": _page_metadata(doc, page_ids),
                "changedPageIds": page_ids,
                "removedPageIds": [],
            }
        finally:
            doc.close()
        destination = output.with_suffix(".pdf")
        shutil.copy2(source, destination)
        return {"kind": "session_mutation", "artifact": destination.name, "data": data}

    if operation.startswith("editor_"):
        doc = _read_pdf(payload["source_path"])
        page_ids = list(payload["page_ids"])
        changed_page_ids: set[str] = set()
        removed_page_ids: list[str] = []
        try:
            _check_cancel(cancel_event)
            if operation == "editor_apply":
                prepared = _prepare_editor_batch(doc, page_ids, payload["edits"])
                for item in prepared:
                    edit = item["edit"]
                    if edit.get("pageId"):
                        changed_page_ids.add(edit["pageId"])
                    if item["no_op"]:
                        continue
                    doc[item["page_no"]].add_redact_annot(item["bbox"], fill=(1, 1, 1))
                edited_pages = {item["page_no"] for item in prepared if not item["no_op"]}
                for page_no in sorted(edited_pages):
                    doc[page_no].apply_redactions(
                        images=fitz.PDF_REDACT_IMAGE_NONE,
                        graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                    )
                for item in prepared:
                    if item["no_op"]:
                        continue
                    edit = item["edit"]
                    page = doc[item["page_no"]]
                    bbox = item["bbox"]
                    text = item["text"]
                    if item["whiteout"]:
                        if edit.get("kind") != "cover":
                            page.draw_rect(bbox, color=None, fill=(1, 1, 1))
                        continue
                    font = pdf_engine.pick_font(text)
                    size = float(edit.get("fontSize") or max(bbox.height * 0.8, 6))
                    while size >= 6:
                        rc = page.insert_textbox(
                            bbox, text, fontname=font, fontsize=size,
                            align=fitz.TEXT_ALIGN_LEFT,
                        )
                        if rc >= 0:
                            break
                        size *= 0.95
                    else:
                        page.insert_text(
                            (bbox.x0 + 1, bbox.y1 - 1), text,
                            fontname=font, fontsize=6,
                        )
            elif operation == "editor_add_text":
                changed_page_ids.add(payload["pageId"])
                page = _validate_anchor(
                    doc, page_ids,
                    {"page": payload["page"], "pageId": payload["pageId"], "kind": "cover"},
                )
                page.insert_text(
                    (payload["x"], payload["y"]),
                    payload["text"],
                    fontname=pdf_engine.pick_font(payload["text"]),
                    fontsize=payload["fontSize"],
                    color=_parse_color(payload.get("color", "#000000")),
                )
            elif operation == "editor_add_image":
                changed_page_ids.add(payload["pageId"])
                page = _validate_anchor(
                    doc, page_ids,
                    {"page": payload["page"], "pageId": payload["pageId"], "kind": "cover"},
                )
                image_path = Path(payload["image_path"])
                validate_image(image_path)
                rect = fitz.Rect(
                    payload["x"], payload["y"],
                    payload["x"] + payload["w"], payload["y"] + payload["h"],
                )
                if not page.rect.contains(rect):
                    raise PdfError(422, "图片区域超出页面范围", code="INVALID_BBOX")
                page.insert_image(rect, filename=str(image_path))
            elif operation == "editor_rotate":
                page_no = int(payload["page"])
                if not 0 <= page_no < doc.page_count:
                    raise PdfError(400, "页码越界")
                doc[page_no].set_rotation(
                    (doc[page_no].rotation + int(payload["angle"])) % 360
                )
            elif operation == "editor_delete":
                indices = sorted(set(int(value) for value in payload["pages"]))
                if any(index < 0 or index >= doc.page_count for index in indices):
                    raise PdfError(400, "页码越界")
                if len(indices) >= doc.page_count:
                    raise PdfError(400, "不能删除全部页面")
                removed_page_ids = [page_ids[index] for index in indices]
                doc.delete_pages(indices)
                removed = set(indices)
                page_ids = [page_id for index, page_id in enumerate(page_ids) if index not in removed]
            elif operation == "editor_move":
                src, dst = int(payload["from"]), int(payload["to"])
                if not 0 <= src < doc.page_count or not 0 <= dst < doc.page_count:
                    raise PdfError(400, "页码越界")
                order = list(range(doc.page_count))
                order.insert(dst, order.pop(src))
                doc.select(order)
                page_ids = [page_ids[index] for index in order]
            else:
                raise PdfError(400, "未知编辑任务", code="UNKNOWN_JOB")

            _check_cancel(cancel_event)
            # Repeated editor commits are latency-sensitive. Benchmarks on
            # 30-60 pages showed garbage=1 preserves the checked content,
            # links, bookmarks, forms, and metadata while avoiding the much
            # slower duplicate-stream sweep of garbage=4. Explicit object
            # optimization still uses garbage=4 below.
            _save_document(doc, output.with_suffix(".pdf"), garbage=1, deflate=True)
            result_doc = _read_pdf(str(output.with_suffix(".pdf")))
            try:
                value = {
                    "pageCount": result_doc.page_count,
                    "pageIds": page_ids,
                    "pages": _page_metadata(result_doc, page_ids),
                    "changedPageIds": sorted(changed_page_ids),
                    "removedPageIds": removed_page_ids,
                }
            finally:
                result_doc.close()
            return {
                "kind": "session_mutation",
                "artifact": output.with_suffix(".pdf").name,
                "data": value,
            }
        finally:
            if not doc.is_closed:
                doc.close()

    if operation == "general_merge":
        sources = payload["source_paths"]
        order = payload["order"]
        names = payload["names"]
        out = fitz.open()
        toc = []
        try:
            for index in order:
                _check_cancel(cancel_event)
                source = _read_pdf(sources[index])
                try:
                    start = out.page_count
                    out.insert_pdf(source)
                    for level, title, page_no in source.get_toc():
                        toc.append([level, title, page_no + start])
                finally:
                    source.close()
            if toc:
                out.set_toc(toc)
            _save_document(out, output.with_suffix(".pdf"), garbage=4, deflate=True)
        finally:
            if not out.is_closed:
                out.close()
        return _file_result(output.with_suffix(".pdf"), f"{_name(names[order[0]])}_merged.pdf")

    if operation == "general_split":
        doc = _read_pdf(payload["source_path"])
        archive = output.with_suffix(".zip")
        base = _name(payload.get("filename"))
        try:
            segments = _parse_segments(payload["page_ranges"], doc.page_count)
            metadata = {
                key: value
                for key, value in (doc.metadata or {}).items()
                if key in {
                    "title", "author", "subject", "keywords", "creator",
                    "producer", "creationDate", "modDate", "trapped",
                }
                and value is not None
            }
            source_toc = doc.get_toc()
            used_names: set[str] = set()
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
                for number, (pages, label) in enumerate(segments, 1):
                    _check_cancel(cancel_event)
                    segment_path = work_dir / f"segment-{number}.pdf"
                    segment = fitz.open()
                    try:
                        segment.insert_pdf(doc, from_page=pages[0], to_page=pages[-1])
                        if metadata:
                            segment.set_metadata(metadata)
                        segment_toc = [
                            [level, title, page_no - pages[0]]
                            for level, title, page_no in source_toc
                            if pages[0] < page_no <= pages[-1] + 1
                        ]
                        if segment_toc:
                            segment.set_toc(segment_toc)
                        _save_document(segment, segment_path, garbage=4, deflate=True)
                    finally:
                        if not segment.is_closed:
                            segment.close()
                    archive_name = f"{base}_pages_{label}.pdf"
                    if archive_name in used_names:
                        archive_name = f"{base}_pages_{label}_{number}.pdf"
                    used_names.add(archive_name)
                    zf.write(segment_path, archive_name)
                    segment_path.unlink(missing_ok=True)
                    _progress(progress_queue, "processing", number, len(segments), "segments")
        finally:
            doc.close()
        return _file_result(archive, f"{base}_split.zip", "application/zip")

    if operation in {"general_rotate", "general_remove", "general_reorder"}:
        doc = _read_pdf(payload["source_path"])
        base = _name(payload.get("filename"))
        try:
            if operation == "general_rotate":
                indices = pdf_engine.parse_page_ranges(payload["pages"], doc.page_count)
                for index in indices:
                    doc[index].set_rotation((doc[index].rotation + payload["angle"]) % 360)
                suffix = "_rotated"
            elif operation == "general_remove":
                indices = set(pdf_engine.parse_page_ranges(payload["pages"], doc.page_count))
                if len(indices) >= doc.page_count:
                    raise PdfError(400, "不能删除全部页面")
                doc.select([index for index in range(doc.page_count) if index not in indices])
                suffix = "_trimmed"
            else:
                try:
                    order = [
                        int(token) - 1
                        for token in payload["page_order"].replace("\n", ",").split(",")
                        if token.strip()
                    ]
                except ValueError as exc:
                    raise PdfError(400, "页序包含无效页码") from exc
                if sorted(order) != list(range(doc.page_count)):
                    raise PdfError(400, "页序必须是完整的页面排列")
                doc.select(order)
                suffix = "_reordered"
            _check_cancel(cancel_event)
            _save_document(doc, output.with_suffix(".pdf"), garbage=4, deflate=True)
        finally:
            if not doc.is_closed:
                doc.close()
        return _file_result(output.with_suffix(".pdf"), f"{base}{suffix}.pdf")

    if operation == "convert_pdf_to_image":
        doc = _read_pdf(payload["source_path"])
        base = _name(payload.get("filename"))
        fmt = payload["format"]
        ext = "png" if fmt == "png" else "jpg"
        indices = pdf_engine.parse_page_ranges(payload["pages"], doc.page_count)
        scale = float(payload["dpi"]) / 72.0
        try:
            if len(indices) == 1:
                _check_cancel(cancel_event)
                page = doc[indices[0]]
                width = math.ceil(page.rect.width * scale)
                height = math.ceil(page.rect.height * scale)
                pixels = width * height
                if pixels > config.RENDER_BATCH_PIXELS:
                    raise PdfError(422, "单页渲染超过 160 MP 在途预算，请降低 DPI", code="RENDER_BUDGET_EXCEEDED")
                image_path = output.with_suffix(f".{ext}")
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
                pix.save(str(image_path))
                pix = None
                return _file_result(image_path, f"{base}_page_{indices[0] + 1}.{ext}",
                                    "image/png" if fmt == "png" else "image/jpeg")

            archive = output.with_suffix(".zip")
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
                for number, page_index in enumerate(indices, 1):
                    _check_cancel(cancel_event)
                    page = doc[page_index]
                    width = math.ceil(page.rect.width * scale)
                    height = math.ceil(page.rect.height * scale)
                    pixels = width * height
                    if pixels > config.RENDER_BATCH_PIXELS:
                        raise PdfError(422, "单页渲染超过 160 MP 在途预算，请降低 DPI", code="RENDER_BUDGET_EXCEEDED")
                    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
                    image_path = work_dir / f"page-{number}.{ext}"
                    pix.save(str(image_path))
                    pix = None
                    zf.write(image_path, f"{base}_page_{page_index + 1}.{ext}")
                    image_path.unlink(missing_ok=True)
                    # Drop native page and pixel handles before rendering the
                    # next page; the batch budget is in-flight, not cumulative.
                    page = None
                    _progress(progress_queue, "processing", number, len(indices), "pages")
            return _file_result(archive, f"{base}_images.zip", "application/zip")
        finally:
            doc.close()

    if operation == "convert_pdf_to_text":
        doc = _read_pdf(payload["source_path"])
        text_path = output.with_suffix(".txt")
        try:
            chunks = []
            for index in range(doc.page_count):
                _check_cancel(cancel_event)
                chunks.append(doc[index].get_text("text"))
                _progress(progress_queue, "processing", index + 1, doc.page_count, "pages")
            text_path.write_text("\n\n".join(chunks), encoding="utf-8")
        finally:
            doc.close()
        return _file_result(text_path, f"{_name(payload.get('filename'))}.txt", "text/plain; charset=utf-8")

    if operation == "convert_pdf_to_word":
        source = Path(payload["source_path"])
        doc = _read_pdf(str(source))
        doc.close()
        try:
            from pdf2docx import Converter
            converter = Converter(str(source))
            try:
                converter.convert(str(output.with_suffix(".docx")))
            finally:
                converter.close()
        except Exception as exc:  # noqa: BLE001
            raise PdfError(500, "PDF 转 Word 失败", code="CONVERSION_FAILED") from exc
        if not output.with_suffix(".docx").exists():
            raise PdfError(500, "PDF 转 Word 输出为空", code="CONVERSION_FAILED")
        return _file_result(
            output.with_suffix(".docx"),
            f"{_name(payload.get('filename'))}.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    if operation == "office_to_pdf":
        import asyncio
        from ..core.tempfile import temp_manager
        from .libreoffice import convert_to_pdf

        converted = asyncio.run(
            convert_to_pdf(
                Path(payload["source_path"]),
                timeout=payload.get("timeout"),
            )
        )
        destination = output.with_suffix(".pdf")
        try:
            shutil.copy2(converted, destination)
        finally:
            temp_manager.discard(converted)
        return _file_result(destination, f"{_name(payload.get('filename'))}.pdf")

    if operation == "convert_image_to_pdf":
        image_paths = payload["image_paths"]
        page_size = payload["pageSize"]
        fit = payload["fit"]
        out = fitz.open()
        try:
            for number, path in enumerate(image_paths, 1):
                _check_cancel(cancel_event)
                image_path = Path(path)
                validate_image(image_path)
                if page_size == "source":
                    pix = fitz.Pixmap(str(image_path))
                    x_dpi, y_dpi = _image_dpi(image_path)
                    page = out.new_page(
                        width=pix.width * 72.0 / x_dpi,
                        height=pix.height * 72.0 / y_dpi,
                    )
                    pix = None
                    page.insert_image(page.rect, filename=str(image_path), keep_proportion=fit)
                else:
                    target = fitz.paper_rect(page_size)
                    page = out.new_page(width=target.width, height=target.height)
                    margin = 36
                    page.insert_image(
                        fitz.Rect(margin, margin, target.width - margin, target.height - margin),
                        filename=str(image_path),
                        keep_proportion=fit,
                    )
                _progress(progress_queue, "processing", number, len(image_paths), "images")
            _save_document(out, output.with_suffix(".pdf"), garbage=4, deflate=True)
        finally:
            if not out.is_closed:
                out.close()
        return _file_result(output.with_suffix(".pdf"), f"{_name(payload.get('filename'))}_images.pdf")

    if operation == "misc_pdf_info":
        source_path = Path(payload["source_path"])
        try:
            doc = fitz.open(str(source_path))
        except Exception:
            return _unknown_pdf_info(source_path)
        try:
            encrypted_status = _encryption_status(doc)
            if encrypted_status is True:
                return {
                    "kind": "json",
                    "data": {
                        "pageCount": None,
                        "pageSize": None,
                        "pdfVersion": (doc.metadata or {}).get("format", ""),
                        "metadata": doc.metadata or {},
                        "isEncrypted": True,
                        "fonts": [],
                        "tocEntries": None,
                        "fileSize": Path(payload["source_path"]).stat().st_size,
                    },
                }
            if encrypted_status is None:
                return _unknown_pdf_info(source_path)
            if doc.page_count == 0:
                raise PdfError(400, "PDF 没有任何页面", code="INVALID_PDF")
            fonts = set()
            for index in range(doc.page_count):
                _check_cancel(cancel_event)
                for item in doc[index].get_fonts():
                    if len(item) > 3 and item[3]:
                        fonts.add(item[3])
                _progress(progress_queue, "processing", index + 1, doc.page_count, "pages")
            page = doc[0]
            return {
                "kind": "json",
                "data": {
                    "pageCount": doc.page_count,
                    "pageSize": {
                        "width": round(page.rect.width, 2),
                        "height": round(page.rect.height, 2),
                        "rotation": page.rotation,
                    },
                    "pdfVersion": doc.metadata.get("format", ""),
                    "metadata": doc.metadata,
                    "isEncrypted": encrypted_status,
                    "fonts": sorted(fonts),
                    "tocEntries": len(doc.get_toc()),
                    "fileSize": Path(payload["source_path"]).stat().st_size,
                },
            }
        finally:
            doc.close()

    if operation == "misc_compress":
        doc = _read_pdf(payload["source_path"])
        try:
            _save_document(doc, output.with_suffix(".pdf"), garbage=4, deflate=True, clean=True)
        finally:
            if not doc.is_closed:
                doc.close()
        return _file_result(output.with_suffix(".pdf"), f"{_name(payload.get('filename'))}_optimized.pdf")

    if operation in {"misc_watermark", "misc_page_numbers"}:
        doc = _read_pdf(payload["source_path"])
        base = _name(payload.get("filename"))
        try:
            indices = pdf_engine.parse_page_ranges(payload["pages"], doc.page_count)
            if operation == "misc_watermark":
                text = payload["text"]
                font_size = payload["fontSize"]
                opacity = payload["opacity"]
                angle = payload["rotation"]
                color = _parse_color(payload["color"])
                font = pdf_engine.pick_font(text)
                width = fitz.get_text_length(text, fontname=font, fontsize=font_size)
                for number, index in enumerate(indices, 1):
                    _check_cancel(cancel_event)
                    page = doc[index]
                    rect = page.rect
                    display_center = fitz.Point(rect.width / 2, rect.height / 2)
                    display_box = fitz.Rect(
                        display_center.x - width / 2 - 20,
                        display_center.y - font_size,
                        display_center.x + width / 2 + 20,
                        display_center.y + font_size,
                    )
                    center = _display_to_page_point(page, display_center)
                    box = _display_textbox_to_page(page, display_box)
                    inserted = page.insert_textbox(
                        box, text, fontname=font, fontsize=font_size, color=color,
                        align=fitz.TEXT_ALIGN_CENTER, overlay=True,
                        fill_opacity=opacity,
                        morph=(center, fitz.Matrix(angle + page.rotation)),
                    )
                    if inserted < 0:
                        raise PdfError(422, f"水印在第 {index + 1} 页无法完整放置", code="WATERMARK_OVERFLOW")
                    _progress(progress_queue, "processing", number, len(indices), "pages")
                filename = f"{base}_watermarked.pdf"
            else:
                position = payload["position"]
                size = payload["fontSize"]
                margin = payload["margin"]
                start = payload["startNumber"]
                for offset, index in enumerate(indices):
                    _check_cancel(cancel_event)
                    page = doc[index]
                    rect = page.rect
                    label = str(start + offset)
                    width = fitz.get_text_length(label, fontname="helv", fontsize=size)
                    y = rect.height - margin if position.startswith("bottom") else margin + size
                    if position.endswith("center"):
                        x = (rect.width - width) / 2
                    elif position.endswith("right"):
                        x = rect.width - margin - width
                    else:
                        x = margin
                    point = _display_to_page_point(page, fitz.Point(x, y))
                    # The point is mapped into page space; rotate the baseline
                    # with the page so it reads left-to-right in display space.
                    page.insert_text(
                        point, label, fontname="helv", fontsize=size,
                        color=(0.35, 0.35, 0.35),
                        rotate=page.rotation,
                    )
                    _progress(progress_queue, "processing", offset + 1, len(indices), "pages")
                filename = f"{base}_numbered.pdf"
            _check_cancel(cancel_event)
            _save_document(doc, output.with_suffix(".pdf"), garbage=4, deflate=True)
        finally:
            if not doc.is_closed:
                doc.close()
        return _file_result(output.with_suffix(".pdf"), filename)

    raise PdfError(400, f"未知任务类型: {operation}", code="UNKNOWN_JOB")


def _file_result(path: Path, filename: str, media_type: str = "application/pdf") -> dict[str, Any]:
    return {
        "kind": "file",
        "artifact": path.name,
        "filename": filename,
        "mediaType": media_type,
    }


def worker_entry(operation: str, payload: dict[str, Any], work_dir: str,
                 result_file: str, progress_queue, cancel_event,
                 memory_limit_bytes: int) -> None:
    """Process target. It always writes a structured result file if possible."""
    try:
        if os.name != "nt":
            try:
                import resource
                resource.setrlimit(
                    resource.RLIMIT_AS,
                    (memory_limit_bytes, memory_limit_bytes),
                )
            except (ImportError, OSError, ValueError):
                pass
        result = _run_operation(
            operation, payload, Path(work_dir), progress_queue, cancel_event
        )
        envelope = {"ok": True, "result": result}
    except MemoryError:
        logger.error("PDF worker exceeded its memory budget for operation %s", operation)
        # Do not continue with a partially usable interpreter after allocation
        # failure; the parent observes the reserved exit code and cleans up.
        os._exit(70)
    except PdfError as exc:
        envelope = {
            "ok": False,
            "error": {"code": exc.code, "message": exc.message},
        }
    except BaseException:
        logger.exception("PDF worker failed for operation %s", operation)
        envelope = {
            "ok": False,
            "error": {"code": "WORKER_FAILED", "message": "PDF 处理任务失败"},
        }
    try:
        _save_result(envelope, Path(result_file))
    except BaseException:
        logger.exception("Could not persist PDF worker result for operation %s", operation)
