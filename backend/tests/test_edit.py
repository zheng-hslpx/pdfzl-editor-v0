"""Tests for the visual editor endpoints (/api/v1/edit/*)."""
from __future__ import annotations

import json

import fitz
import pytest

from conftest import make_sample_pdf, make_zh_pdf


def _open(client, pdf_path, name="sample.pdf"):
    r = client.post(
        "/api/v1/edit/open",
        files=[("fileInput", (name, pdf_path.read_bytes(), "application/pdf"))],
    )
    assert r.status_code == 200
    return r.json()


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


def test_open_returns_text_model(client, sample_pdf):
    data = _open(client, sample_pdf)
    assert data["pageCount"] == 3
    assert data["pages"][0]["width"] == 595
    line = _find_line(data["textModel"], 0, "PAGE-1")
    assert line["bbox"][2] > line["bbox"][0]
    assert line["fontSize"] > 0
    assert line["font"] == "helv"


def test_replace_text_roundtrip(client, sample_pdf):
    data = _open(client, sample_pdf)
    doc_id = data["docId"]
    line = _find_line(data["textModel"], 0, "PAGE-1")
    r = client.post(
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
    assert r.status_code == 200
    out = _download(client, doc_id)
    text = out[0].get_text()
    assert "REPLACED TEXT" in text
    assert "PAGE-1" not in text
    out.close()


def test_replace_with_chinese(client, sample_pdf):
    data = _open(client, sample_pdf)
    doc_id = data["docId"]
    line = _find_line(data["textModel"], 0, "HELLO")
    r = client.post(
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
    assert r.status_code == 200
    out = _download(client, doc_id)
    assert "你好世界" in out[0].get_text()
    out.close()


def test_whiteout_via_empty_text(client, sample_pdf):
    data = _open(client, sample_pdf)
    doc_id = data["docId"]
    line = _find_line(data["textModel"], 0, "PAGE-1")
    r = client.post(
        f"/api/v1/edit/{doc_id}/apply",
        json={
            "edits": [
                _edit_payload(line, 0, "")
            ],
            "expectedRevision": data["revision"],
        },
    )
    assert r.status_code == 200
    out = _download(client, doc_id)
    assert "PAGE-1" not in out[0].get_text()
    out.close()


def test_add_text_chinese(client, sample_pdf):
    data = _open(client, sample_pdf)
    doc_id = data["docId"]
    r = client.post(
        f"/api/v1/edit/{doc_id}/text/add",
        json={"expectedRevision": data["revision"], "page": 0,
              "pageId": data["pages"][0]["pageId"], "x": 72, "y": 400,
              "text": "新增的中文文字", "fontSize": 14},
    )
    assert r.status_code == 200
    out = _download(client, doc_id)
    assert "新增的中文文字" in out[0].get_text()
    out.close()


def test_add_image(client, sample_pdf, sample_png):
    data = _open(client, sample_pdf)
    doc_id = data["docId"]
    r = client.post(
        f"/api/v1/edit/{doc_id}/image/add",
        files=[("image", ("stamp.png", sample_png.read_bytes(), "image/png"))],
        data={"page": 0, "x": 100, "y": 500, "w": 120, "h": 80,
              "expectedRevision": data["revision"], "pageId": data["pages"][0]["pageId"]},
    )
    assert r.status_code == 200
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
    )
    assert bad.status_code == 422
    assert bad.json()["code"] == "INVALID_IMAGE"

    good = client.post(
        f"/api/v1/edit/{doc_id}/text/add",
        json={"expectedRevision": data["revision"], "page": 0,
              "pageId": data["pages"][0]["pageId"], "x": 72, "y": 400,
              "text": "session remains usable"},
    )
    assert good.status_code == 200


def test_page_rotate_delete_move(client, sample_pdf):
    data = _open(client, sample_pdf)
    doc_id = data["docId"]

    r = client.post(f"/api/v1/edit/{doc_id}/pages/rotate", json={
        "page": 0, "angle": 90, "expectedRevision": data["revision"]
    })
    assert r.status_code == 200
    assert r.json()["pages"][0]["rotation"] == 90

    r = client.post(f"/api/v1/edit/{doc_id}/pages/delete", json={
        "pages": [1], "expectedRevision": r.json()["revision"]
    })
    assert r.status_code == 200
    assert r.json()["pageCount"] == 2

    r = client.post(f"/api/v1/edit/{doc_id}/pages/move", json={
        "from": 1, "to": 0, "expectedRevision": r.json()["revision"]
    })
    assert r.status_code == 200
    out = _download(client, doc_id)
    # original page 3 should now be first
    assert "PAGE-3" in out[0].get_text()
    out.close()


def test_delete_all_pages_rejected(client, sample_pdf):
    data = _open(client, sample_pdf)
    r = client.post(
        f"/api/v1/edit/{data['docId']}/pages/delete",
        json={"pages": [0, 1, 2], "expectedRevision": data["revision"]},
    )
    assert r.status_code == 400


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
        headers={"content-type": "application/json"},
    )
    assert invalid_bbox.status_code == 422
    assert invalid_bbox.json()["code"] == "INVALID_INPUT"
    assert client.get(f"/api/v1/edit/{doc_id}/download").content == before

    bad_anchor = _edit_payload(line, 0, "SHOULD NOT WRITE")
    bad_anchor["originalHash"] = "0" * 64
    response = client.post(
        f"/api/v1/edit/{doc_id}/apply",
        json={"expectedRevision": data["revision"], "edits": [bad_anchor]},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "REVISION_CONFLICT"
    assert client.get(f"/api/v1/edit/{doc_id}/download").content == before


@pytest.mark.parametrize("angle", [90, 180, 270])
def test_rotated_pages_reject_coordinate_writes(client, sample_pdf, sample_png, angle):
    data = _open(client, sample_pdf)
    doc_id = data["docId"]
    rotated = client.post(
        f"/api/v1/edit/{doc_id}/pages/rotate",
        json={"page": 0, "angle": angle, "expectedRevision": data["revision"]},
    )
    assert rotated.status_code == 200
    revision = rotated.json()["revision"]
    page_id = rotated.json()["pages"][0]["pageId"]
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
    )
    assert cover.status_code == 422

    image = client.post(
        f"/api/v1/edit/{doc_id}/image/add",
        files=[("image", ("stamp.png", sample_png.read_bytes(), "image/png"))],
        data={"page": 0, "x": 100, "y": 500, "w": 120, "h": 80,
              "expectedRevision": revision, "pageId": page_id},
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
    )
    assert r.status_code == 400
    assert set(config.TEMP_DIR.glob("pdfeditor-*")) == before
