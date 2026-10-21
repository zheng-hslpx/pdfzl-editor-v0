"""Tests for the visual editor endpoints (/api/v1/edit/*)."""
from __future__ import annotations

import gzip
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import fitz
import pytest

from conftest import make_sample_pdf, make_zh_pdf, submit_job


def _open(client, pdf_path, name="sample.pdf"):
    result, _ = submit_job(
        client,
        "/api/v1/edit/open",
        files=[("fileInput", (name, pdf_path.read_bytes(), "application/pdf"))],
    )
    assert result["state"] == "succeeded", result.get("error")
    data = result["result"]
    data["textModel"] = [
        client.get(f"/api/v1/edit/{data['docId']}/pages/{page['page']}/text").json()["page"]
        for page in data["pages"]
    ]
    return data


def _find_line(model: dict, page: int, needle: str) -> dict:
    for line in model[page]["lines"]:
        if needle in line["text"]:
            return line
    raise AssertionError(f"line containing {needle!r} not found on page {page}")


def _edit_payload(line: dict, page: int, text: str) -> dict:
    return {
        "page": page,
        "pageId": line["pageId"],
        "lineId": line["id"],
        "originalHash": line["originalHash"],
        "bbox": line["bbox"],
        "original": line["text"],
        "text": text,
        "fontSize": line["fontSize"],
    }


def _download(client, doc_id: str) -> fitz.Document:
    r = client.get(f"/api/v1/edit/{doc_id}/download")
    assert r.status_code == 200
    return fitz.open(stream=r.content, filetype="pdf")


def test_open_returns_light_pages_and_paged_text_api(client, sample_pdf):
    accepted, _ = submit_job(
        client,
        "/api/v1/edit/open",
        files=[("fileInput", ("sample.pdf", sample_pdf.read_bytes(), "application/pdf"))],
    )
    assert accepted["state"] == "succeeded"
    data = accepted["result"]
    assert "textModel" not in data
    assert data["pageCount"] == 3
    assert data["pages"][0]["width"] == 595
    page_slice = client.get(
        f"/api/v1/edit/{data['docId']}/pages", params={"offset": 1, "limit": 1}
    )
    assert page_slice.status_code == 200
    assert page_slice.json()["pages"][0]["page"] == 1
    page_response = client.get(f"/api/v1/edit/{data['docId']}/pages/0/text")
    assert page_response.status_code == 200
    assert page_response.json()["revision"] == data["revision"]
    line = _find_line([page_response.json()["page"]], 0, "PAGE-1")
    assert line["bbox"][2] > line["bbox"][0]
    assert line["fontSize"] > 0
    assert line["font"] == "helv"


def test_dense_page_text_worker_keeps_health_responsive(client):
    source_path = Path(__file__).parent / "fixtures" / "dense-6000-lines.pdf.gz"

    opened, _ = submit_job(
        client,
        "/api/v1/edit/open",
        files=[
            (
                "fileInput",
                ("dense-6000-lines.pdf", gzip.decompress(source_path.read_bytes()), "application/pdf"),
            )
        ],
    )
    assert opened["state"] == "succeeded", opened.get("error")
    doc_id = opened["result"]["docId"]

    with ThreadPoolExecutor(max_workers=1) as pool:
        text_request = pool.submit(
            client.get, f"/api/v1/edit/{doc_id}/pages/0/text"
        )
        time.sleep(0.03)
        started = time.monotonic()
        health = client.get("/api/health")
        health_elapsed = time.monotonic() - started
        response = text_request.result(timeout=15)

    assert health.status_code == 200
    assert health_elapsed < 0.25
    assert response.status_code == 200, response.text[:500]
    assert len(response.json()["page"]["lines"]) == 6000


def test_concurrent_page_text_reads_wait_for_the_bounded_worker_slot(client, sample_pdf):
    opened, _ = submit_job(
        client,
        "/api/v1/edit/open",
        files=[("fileInput", ("sample.pdf", sample_pdf.read_bytes(), "application/pdf"))],
    )
    assert opened["state"] == "succeeded", opened.get("error")
    doc_id = opened["result"]["docId"]

    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(
            pool.map(
                lambda page_no: client.get(
                    f"/api/v1/edit/{doc_id}/pages/{page_no}/text"
                ),
                range(3),
            )
        )

    assert [response.status_code for response in responses] == [200, 200, 200]
    assert [response.json()["page"]["page"] for response in responses] == [0, 1, 2]


def test_large_editor_open_paginates_metadata_without_a_page_cap(client, tmp_path):
    source_path = tmp_path / "one-hundred-five-pages.pdf"
    source = fitz.open()
    for index in range(105):
        page = source.new_page(width=595, height=842)
        page.insert_text((72, 100), f"PAGE-{index + 1}")
    source.save(source_path)
    source.close()

    opened, _ = submit_job(
        client,
        "/api/v1/edit/open",
        files=[("fileInput", (source_path.name, source_path.read_bytes(), "application/pdf"))],
    )
    assert opened["state"] == "succeeded", opened.get("error")
    data = opened["result"]
    assert data["pageCount"] == 105
    assert len(data["pages"]) == 50
    assert "textModel" not in data

    second_slice = client.get(
        f"/api/v1/edit/{data['docId']}/pages", params={"offset": 50, "limit": 50}
    ).json()
    final_slice = client.get(
        f"/api/v1/edit/{data['docId']}/pages", params={"offset": 100, "limit": 50}
    ).json()
    assert [len(second_slice["pages"]), len(final_slice["pages"])] == [50, 5]
    last_page = client.get(f"/api/v1/edit/{data['docId']}/pages/104/text").json()
    assert last_page["page"]["pageId"] == final_slice["pages"][-1]["pageId"]
    assert any("PAGE-105" in line["text"] for line in last_page["page"]["lines"])


def test_undo_redo_uses_inverse_records_and_server_snapshots(client, sample_pdf):
    data = _open(client, sample_pdf)
    doc_id = data["docId"]

    rotated, _ = submit_job(
        client,
        f"/api/v1/edit/{doc_id}/pages/rotate",
        json={"page": 0, "angle": 90, "expectedRevision": data["revision"]},
    )
    assert rotated["state"] == "succeeded"
    assert client.get(f"/api/v1/edit/{doc_id}/history").json()["undoSteps"] == 1
    assert client.get(f"/api/v1/edit/{doc_id}/history").json()["historyBytes"] == 0

    undone_rotation, _ = submit_job(
        client,
        f"/api/v1/edit/{doc_id}/history/undo",
        json={"expectedRevision": rotated["result"]["revision"]},
    )
    assert undone_rotation["state"] == "succeeded"
    current = _download(client, doc_id)
    assert current[0].rotation == 0
    current.close()
    assert client.get(f"/api/v1/edit/{doc_id}/history").json()["canRedo"] is True

    redone_rotation, _ = submit_job(
        client,
        f"/api/v1/edit/{doc_id}/history/redo",
        json={"expectedRevision": undone_rotation["result"]["revision"]},
    )
    assert redone_rotation["state"] == "succeeded"
    current = _download(client, doc_id)
    assert current[0].rotation == 90
    current.close()

    deleted, _ = submit_job(
        client,
        f"/api/v1/edit/{doc_id}/pages/delete",
        json={"pages": [1], "expectedRevision": redone_rotation["result"]["revision"]},
    )
    assert deleted["state"] == "succeeded"
    history = client.get(f"/api/v1/edit/{doc_id}/history").json()
    assert history["historyBytes"] > 0
    restored, _ = submit_job(
        client,
        f"/api/v1/edit/{doc_id}/history/undo",
        json={"expectedRevision": deleted["result"]["revision"]},
    )
    assert restored["state"] == "succeeded"
    current = _download(client, doc_id)
    assert current.page_count == 3
    current.close()
    redeleted, _ = submit_job(
        client,
        f"/api/v1/edit/{doc_id}/history/redo",
        json={"expectedRevision": restored["result"]["revision"]},
    )
    assert redeleted["state"] == "succeeded"
    current = _download(client, doc_id)
    assert current.page_count == 2
    current.close()


def test_editor_commit_preserves_links_forms_bookmarks_and_metadata(client, tmp_path):
    source_path = tmp_path / "document-objects.pdf"
    source = fitz.open()
    page = source.new_page()
    page.insert_text((72, 100), "PRESERVE OBJECTS")
    page.insert_link({
        "kind": fitz.LINK_URI,
        "from": fitz.Rect(72, 90, 210, 110),
        "uri": "https://example.invalid/editor-test",
    })
    widget = fitz.Widget()
    widget.field_name = "editor-preserved-field"
    widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    widget.rect = fitz.Rect(72, 140, 240, 164)
    widget.field_value = "keep this field"
    page.add_widget(widget)
    source.set_metadata({"title": "Editor commit object check", "author": "PDF Editor"})
    source.set_toc([[1, "Bookmark survives edit", 1]])
    source.save(source_path)
    source.close()

    opened = _open(client, source_path, "document-objects.pdf")
    job, _ = submit_job(
        client,
        f"/api/v1/edit/{opened['docId']}/pages/rotate",
        json={"page": 0, "angle": 90, "expectedRevision": opened["revision"]},
    )
    assert job["state"] == "succeeded", job.get("error")

    result = _download(client, opened["docId"])
    assert result.page_count == 1
    assert result[0].rotation == 90
    assert result.metadata["title"] == "Editor commit object check"
    assert result.get_toc() == [[1, "Bookmark survives edit", 1]]
    assert len(result[0].get_links()) == 1
    widgets = list(result[0].widgets() or [])
    assert len(widgets) == 1
    assert widgets[0].field_value == "keep this field"
    result.close()


def test_replace_text_roundtrip(client, sample_pdf):
    data = _open(client, sample_pdf)
    doc_id = data["docId"]
    line = _find_line(data["textModel"], 0, "PAGE-1")
    job, _ = submit_job(client,
        f"/api/v1/edit/{doc_id}/apply",
        json={
            "edits": [
                {
                    **_edit_payload(line, 0, "REPLACED TEXT"),
                }
            ],
            "expectedRevision": data["revision"],
        },
    )
    assert job["state"] == "succeeded", job.get("error")
    out = _download(client, doc_id)
    text = out[0].get_text()
    assert "REPLACED TEXT" in text
    assert "PAGE-1" not in text
    out.close()


def test_apply_batch_replaces_multiple_regions_and_deletes_empty(client, tmp_path):
    source_path = tmp_path / "multi-line-batch.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=500)
    page.insert_text((40, 70), "FIRST ORIGINAL", fontsize=14)
    page.insert_text((40, 110), "SECOND ORIGINAL", fontsize=14)
    page.insert_text((40, 150), "中文原文", fontsize=14, fontname="china-s")
    page.insert_text((40, 190), "DELETE EMPTY", fontsize=14)
    page.insert_text((40, 230), "KEEP ORIGINAL", fontsize=14)
    doc.save(source_path)
    doc.close()

    data = _open(client, source_path)
    page_lines = data["textModel"][0]["lines"]
    replacements = {
        "FIRST ORIGINAL": "REPLACEMENT ONE",
        "SECOND ORIGINAL": "第二行替换",
        "中文原文": "你好世界",
        "DELETE EMPTY": "",
    }
    edits = [
        _edit_payload(next(line for line in page_lines if needle in line["text"]), 0, value)
        for needle, value in replacements.items()
    ]
    job, _ = submit_job(
        client,
        f"/api/v1/edit/{data['docId']}/apply",
        json={"expectedRevision": data["revision"], "edits": edits},
    )

    assert job["state"] == "succeeded", job.get("error")
    assert job["result"]["revision"] == data["revision"] + 1
    out = _download(client, data["docId"])
    text = out[0].get_text()
    for replacement in ("REPLACEMENT ONE", "第二行替换", "你好世界", "KEEP ORIGINAL"):
        assert replacement in text
    for original in ("FIRST ORIGINAL", "SECOND ORIGINAL", "中文原文", "DELETE EMPTY"):
        assert original not in text
    out.close()


def test_apply_batch_rejects_overlapping_regions_without_partial_commit(client, tmp_path):
    source_path = tmp_path / "overlapping-batch.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=500)
    page.insert_text((40, 70), "OVERLAP ORIGINAL", fontsize=14)
    page.insert_text((40, 120), "UNCHANGED SECOND ROW", fontsize=14)
    doc.save(source_path)
    doc.close()

    data = _open(client, source_path)
    line = next(line for line in data["textModel"][0]["lines"] if "OVERLAP ORIGINAL" in line["text"])
    before = client.get(f"/api/v1/edit/{data['docId']}/download").content
    job, _ = submit_job(
        client,
        f"/api/v1/edit/{data['docId']}/apply",
        json={
            "expectedRevision": data["revision"],
            "edits": [
                _edit_payload(line, 0, "FIRST OVERLAPPING VALUE"),
                _edit_payload(line, 0, "SECOND OVERLAPPING VALUE"),
            ],
        },
    )

    assert job["state"] == "failed"
    assert job["error"]["code"] == "OVERLAPPING_EDITS"
    assert client.get(f"/api/v1/edit/{data['docId']}/download").content == before
    page_after = client.get(f"/api/v1/edit/{data['docId']}/pages/0/text").json()
    assert page_after["revision"] == data["revision"]
    assert any(item["text"] == "OVERLAP ORIGINAL" for item in page_after["page"]["lines"])
    assert any(item["text"] == "UNCHANGED SECOND ROW" for item in page_after["page"]["lines"])


@pytest.mark.parametrize(
    "replacement",
    [
        "LONG ENGLISH REPLACEMENT " * 80,
        "超长中文替换内容" * 120,
    ],
    ids=["long-english", "long-chinese"],
)
def test_apply_rejects_replacement_that_cannot_fit_without_committing(client, tmp_path, replacement):
    source_path = tmp_path / "overflow-replacement.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=500)
    page.insert_text((40, 70), "ORIGINAL TARGET", fontsize=14)
    doc.save(source_path)
    doc.close()

    data = _open(client, source_path)
    line = _find_line(data["textModel"], 0, "ORIGINAL TARGET")
    doc_id = data["docId"]
    before_pdf = client.get(f"/api/v1/edit/{doc_id}/download").content
    before_history = client.get(f"/api/v1/edit/{doc_id}/history").json()

    job, _ = submit_job(
        client,
        f"/api/v1/edit/{doc_id}/apply",
        json={
            "expectedRevision": data["revision"],
            "edits": [_edit_payload(line, 0, replacement)],
        },
    )

    assert job["state"] == "failed"
    assert job["error"]["code"] == "TEXT_OVERFLOW"
    assert "无法完整放入编辑区域" in job["error"]["message"]
    assert client.get(f"/api/v1/edit/{doc_id}/download").content == before_pdf
    after_text = client.get(f"/api/v1/edit/{doc_id}/pages/0/text").json()
    assert after_text["revision"] == data["revision"]
    assert any(item["text"] == "ORIGINAL TARGET" for item in after_text["page"]["lines"])
    assert client.get(f"/api/v1/edit/{doc_id}/history").json() == before_history


def test_apply_multiline_replacement_that_fits_keeps_every_line(client, tmp_path):
    source_path = tmp_path / "multiline-replacement.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=500)
    page.insert_text((40, 100), "ORIGINAL TARGET", fontsize=22)
    doc.save(source_path)
    doc.close()

    data = _open(client, source_path)
    line = _find_line(data["textModel"], 0, "ORIGINAL TARGET")
    job, _ = submit_job(
        client,
        f"/api/v1/edit/{data['docId']}/apply",
        json={
            "expectedRevision": data["revision"],
            "edits": [_edit_payload(line, 0, "FIRST LINE\nSECOND LINE")],
        },
    )

    assert job["state"] == "succeeded", job.get("error")
    output = _download(client, data["docId"])
    text = output[0].get_text()
    assert "FIRST LINE" in text
    assert "SECOND LINE" in text
    assert "ORIGINAL TARGET" not in text
    output.close()


def test_apply_batch_overflow_rejects_all_edits_without_history_change(client, tmp_path):
    source_path = tmp_path / "partially-overflowing-batch.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=500)
    page.insert_text((40, 70), "FIRST ORIGINAL", fontsize=14)
    page.insert_text((40, 130), "SECOND ORIGINAL", fontsize=14)
    doc.save(source_path)
    doc.close()

    data = _open(client, source_path)
    lines = data["textModel"][0]["lines"]
    first = _find_line([data["textModel"][0]], 0, "FIRST ORIGINAL")
    second = _find_line([data["textModel"][0]], 0, "SECOND ORIGINAL")
    doc_id = data["docId"]
    before_pdf = client.get(f"/api/v1/edit/{doc_id}/download").content
    before_history = client.get(f"/api/v1/edit/{doc_id}/history").json()
    assert len(lines) == 2

    job, _ = submit_job(
        client,
        f"/api/v1/edit/{doc_id}/apply",
        json={
            "expectedRevision": data["revision"],
            "edits": [
                _edit_payload(first, 0, "VALID FIRST REPLACEMENT"),
                _edit_payload(second, 0, "OVERFLOW " * 300),
            ],
        },
    )

    assert job["state"] == "failed"
    assert job["error"]["code"] == "TEXT_OVERFLOW"
    assert client.get(f"/api/v1/edit/{doc_id}/download").content == before_pdf
    after_text = client.get(f"/api/v1/edit/{doc_id}/pages/0/text").json()
    assert after_text["revision"] == data["revision"]
    assert {item["text"] for item in after_text["page"]["lines"]} == {
        "FIRST ORIGINAL", "SECOND ORIGINAL",
    }
    assert client.get(f"/api/v1/edit/{doc_id}/history").json() == before_history


def test_replace_with_chinese(client, sample_pdf):
    data = _open(client, sample_pdf)
    doc_id = data["docId"]
    line = _find_line(data["textModel"], 0, "HELLO")
    job, _ = submit_job(client,
        f"/api/v1/edit/{doc_id}/apply",
        json={
            "edits": [
                {
                    **_edit_payload(line, 0, "你好世界"),
                }
            ],
            "expectedRevision": data["revision"],
        },
    )
    assert job["state"] == "succeeded", job.get("error")
    out = _download(client, doc_id)
    assert "你好世界" in out[0].get_text()
    out.close()


def test_whiteout_via_empty_text(client, sample_pdf):
    data = _open(client, sample_pdf)
    doc_id = data["docId"]
    line = _find_line(data["textModel"], 0, "PAGE-1")
    job, _ = submit_job(client,
        f"/api/v1/edit/{doc_id}/apply",
        json={
            "edits": [
                _edit_payload(line, 0, "")
            ],
            "expectedRevision": data["revision"],
        },
    )
    assert job["state"] == "succeeded", job.get("error")
    out = _download(client, doc_id)
    assert "PAGE-1" not in out[0].get_text()
    out.close()


def test_add_text_chinese(client, sample_pdf):
    data = _open(client, sample_pdf)
    doc_id = data["docId"]
    job, _ = submit_job(client,
        f"/api/v1/edit/{doc_id}/text/add",
        json={"expectedRevision": data["revision"], "page": 0,
              "pageId": data["pages"][0]["pageId"], "x": 72, "y": 400,
              "text": "新增的中文文字", "fontSize": 14},
    )
    assert job["state"] == "succeeded", job.get("error")
    out = _download(client, doc_id)
    assert "新增的中文文字" in out[0].get_text()
    out.close()


def test_add_image(client, sample_pdf, sample_png):
    data = _open(client, sample_pdf)
    doc_id = data["docId"]
    job, _ = submit_job(client,
        f"/api/v1/edit/{doc_id}/image/add",
        files=[("image", ("stamp.png", sample_png.read_bytes(), "image/png"))],
        data={"page": 0, "x": 100, "y": 500, "w": 120, "h": 80,
              "expectedRevision": data["revision"], "pageId": data["pages"][0]["pageId"]},
    )
    assert job["state"] == "succeeded", job.get("error")
    out = _download(client, doc_id)
    assert out.page_count == 3
    out.close()


def test_invalid_image_does_not_poison_edit_session(client, sample_pdf):
    data = _open(client, sample_pdf)
    doc_id = data["docId"]
    bad = client.post(
        f"/api/v1/edit/{doc_id}/image/add",
        files=[("image", ("fake.png", b"not an image", "image/png"))],
        data={"page": 0, "x": 100, "y": 500, "w": 120, "h": 80,
              "expectedRevision": data["revision"], "pageId": data["pages"][0]["pageId"]},
        headers={"Idempotency-Key": "bad-image-upload"},
    )
    assert bad.status_code == 422
    assert bad.json()["code"] == "INVALID_IMAGE"

    good, _ = submit_job(client,
        f"/api/v1/edit/{doc_id}/text/add",
        json={"expectedRevision": data["revision"], "page": 0,
              "pageId": data["pages"][0]["pageId"], "x": 72, "y": 400,
              "text": "session remains usable"},
    )
    assert good["state"] == "succeeded"


def test_page_rotate_delete_move(client, sample_pdf):
    data = _open(client, sample_pdf)
    doc_id = data["docId"]

    job, _ = submit_job(client, f"/api/v1/edit/{doc_id}/pages/rotate", json={
        "page": 0, "angle": 90, "expectedRevision": data["revision"]
    })
    assert job["state"] == "succeeded"
    revision = job["result"]["revision"]
    assert job["result"]["pages"][0]["rotation"] == 90

    job, _ = submit_job(client, f"/api/v1/edit/{doc_id}/pages/delete", json={
        "pages": [1], "expectedRevision": revision
    })
    assert job["state"] == "succeeded"
    assert job["result"]["pageCount"] == 2

    job, _ = submit_job(client, f"/api/v1/edit/{doc_id}/pages/move", json={
        "from": 1, "to": 0, "expectedRevision": job["result"]["revision"]
    })
    assert job["state"] == "succeeded"
    out = _download(client, doc_id)
    # original page 3 should now be first
    assert "PAGE-3" in out[0].get_text()
    out.close()


def test_delete_all_pages_rejected(client, sample_pdf):
    data = _open(client, sample_pdf)
    result, _ = submit_job(client,
        f"/api/v1/edit/{data['docId']}/pages/delete",
        json={"pages": [0, 1, 2], "expectedRevision": data["revision"]},
    )
    assert result["state"] == "failed"
    assert result["error"]["code"] == "INVALID_INPUT"


def test_close_invalidates_session(client, sample_pdf):
    data = _open(client, sample_pdf)
    doc_id = data["docId"]
    r = client.post(f"/api/v1/edit/{doc_id}/close")
    assert r.status_code == 200
    # close is idempotent, but the session is gone: download must 404
    r = client.get(f"/api/v1/edit/{doc_id}/download")
    assert r.status_code == 404
    r = client.post(f"/api/v1/edit/{doc_id}/close")
    assert r.status_code == 200


def test_chinese_pdf_model(client, zh_pdf):
    data = _open(client, zh_pdf)
    line = _find_line(data["textModel"], 0, "你好世界")
    assert line["font"] == "china-s"


def test_stale_revision_and_line_anchor_are_rejected_without_mutation(client, sample_pdf):
    data = _open(client, sample_pdf)
    doc_id = data["docId"]
    line = _find_line(data["textModel"], 0, "PAGE-1")
    before = client.get(f"/api/v1/edit/{doc_id}/download").content

    stale = client.post(
        f"/api/v1/edit/{doc_id}/apply",
        json={
            "expectedRevision": data["revision"] + 1,
            "edits": [_edit_payload(line, 0, "SHOULD NOT WRITE")],
        },
        headers={"Idempotency-Key": "stale-revision"},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "REVISION_CONFLICT"
    assert client.get(f"/api/v1/edit/{doc_id}/download").content == before

    invalid_bbox = client.post(
        f"/api/v1/edit/{doc_id}/apply",
        content=json.dumps({
            "expectedRevision": data["revision"],
            "edits": [{
                **_edit_payload(line, 0, "SHOULD NOT WRITE"),
                "bbox": [float("nan"), 2, 3, 4],
            }],
        }),
        headers={"content-type": "application/json", "Idempotency-Key": "invalid-bbox"},
    )
    assert invalid_bbox.status_code == 422
    assert invalid_bbox.json()["code"] == "INVALID_INPUT"
    assert client.get(f"/api/v1/edit/{doc_id}/download").content == before

    bad_anchor = _edit_payload(line, 0, "SHOULD NOT WRITE")
    bad_anchor["originalHash"] = "0" * 64
    response = client.post(
        f"/api/v1/edit/{doc_id}/apply",
        json={"expectedRevision": data["revision"], "edits": [bad_anchor]},
        headers={"Idempotency-Key": "bad-line-anchor"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "REVISION_CONFLICT"
    assert client.get(f"/api/v1/edit/{doc_id}/download").content == before


def test_apply_idempotency_retry_precedes_revision_preflight(client, sample_pdf):
    data = _open(client, sample_pdf)
    doc_id = data["docId"]
    line = _find_line(data["textModel"], 0, "PAGE-1")
    body = {
        "expectedRevision": data["revision"],
        "edits": [_edit_payload(line, 0, "IDEMPOTENT RESULT")],
    }
    key = "apply-idempotency-retry"
    url = f"/api/v1/edit/{doc_id}/apply"

    first = client.post(url, json=body, headers={"Idempotency-Key": key})
    assert first.status_code == 202, first.text
    job_id = first.json()["jobId"]

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        status = client.get(f"/api/v1/jobs/{job_id}")
        assert status.status_code == 200, status.text
        job = status.json()
        if job["state"] in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(0.01)
    assert job["state"] == "succeeded", job.get("error")
    assert job["result"]["revision"] == data["revision"] + 1

    retry = client.post(url, json=body, headers={"Idempotency-Key": key})
    assert retry.status_code == 202, retry.text
    assert retry.json() == {"jobId": job_id, "state": "succeeded"}

    changed = {
        **body,
        "edits": [_edit_payload(line, 0, "DIFFERENT REQUEST")],
    }
    mismatch = client.post(url, json=changed, headers={"Idempotency-Key": key})
    assert mismatch.status_code == 409
    assert mismatch.json()["code"] == "IDEMPOTENCY_KEY_REUSED"

    stale_new_request = client.post(
        url, json=body, headers={"Idempotency-Key": "stale-new-request"}
    )
    assert stale_new_request.status_code == 409
    assert stale_new_request.json()["code"] == "REVISION_CONFLICT"


@pytest.mark.parametrize("angle", [90, 180, 270])
def test_rotated_pages_reject_coordinate_writes(client, sample_pdf, sample_png, angle):
    data = _open(client, sample_pdf)
    doc_id = data["docId"]
    rotated, _ = submit_job(client,
        f"/api/v1/edit/{doc_id}/pages/rotate",
        json={"page": 0, "angle": angle, "expectedRevision": data["revision"]},
    )
    assert rotated["state"] == "succeeded"
    revision = rotated["result"]["revision"]
    page_id = rotated["result"]["pages"][0]["pageId"]
    before = client.get(f"/api/v1/edit/{doc_id}/download").content

    add_text = client.post(
        f"/api/v1/edit/{doc_id}/text/add",
        json={
            "expectedRevision": revision,
            "page": 0,
            "pageId": page_id,
            "x": 30,
            "y": 50,
            "text": "must not be written",
        },
        headers={"Idempotency-Key": f"rotated-text-{angle}"},
    )
    assert add_text.status_code == 422
    assert add_text.json()["code"] == "ROTATED_PAGE_UNSUPPORTED"

    cover = client.post(
        f"/api/v1/edit/{doc_id}/apply",
        json={
            "expectedRevision": revision,
            "edits": [{
                "kind": "cover", "page": 0, "pageId": page_id,
                "bbox": [10, 10, 40, 40], "text": "",
            }],
        },
        headers={"Idempotency-Key": f"rotated-cover-{angle}"},
    )
    assert cover.status_code == 422
    assert cover.json()["code"] == "ROTATED_PAGE_UNSUPPORTED"

    image = client.post(
        f"/api/v1/edit/{doc_id}/image/add",
        files=[("image", ("stamp.png", sample_png.read_bytes(), "image/png"))],
        data={"page": 0, "x": 100, "y": 500, "w": 120, "h": 80,
              "expectedRevision": revision, "pageId": page_id},
        headers={"Idempotency-Key": f"rotated-image-{angle}"},
    )
    assert image.status_code == 422
    assert image.json()["code"] == "ROTATED_PAGE_UNSUPPORTED"
    assert client.get(f"/api/v1/edit/{doc_id}/download").content == before


def test_open_invalid_pdf(client, tmp_path):
    from app import config

    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf at all")
    before = set(config.TEMP_DIR.glob("pdfeditor-*"))
    r = client.post(
        "/api/v1/edit/open",
        files=[("fileInput", ("bad.pdf", bad.read_bytes(), "application/pdf"))],
        headers={"Idempotency-Key": "invalid-pdf-open"},
    )
    assert r.status_code == 400
    assert set(config.TEMP_DIR.glob("pdfeditor-*")) == before
