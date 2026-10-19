"""Tests for the visual editor endpoints (/api/v1/edit/*)."""
from __future__ import annotations

import fitz

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
                    "page": 0,
                    "bbox": line["bbox"],
                    "original": line["text"],
                    "text": "REPLACED TEXT",
                    "fontSize": line["fontSize"],
                }
            ]
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
                    "page": 0,
                    "bbox": line["bbox"],
                    "original": line["text"],
                    "text": "你好世界",
                }
            ]
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
                {"page": 0, "bbox": line["bbox"], "original": line["text"], "text": ""}
            ]
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
        json={"page": 0, "x": 72, "y": 400, "text": "新增的中文文字", "fontSize": 14},
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
        data={"page": 0, "x": 100, "y": 500, "w": 120, "h": 80},
    )
    assert r.status_code == 200
    out = _download(client, doc_id)
    assert out.page_count == 3
    out.close()


def test_page_rotate_delete_move(client, sample_pdf):
    data = _open(client, sample_pdf)
    doc_id = data["docId"]

    r = client.post(f"/api/v1/edit/{doc_id}/pages/rotate", json={"page": 0, "angle": 90})
    assert r.status_code == 200
    assert r.json()["pages"][0]["rotation"] == 90

    r = client.post(f"/api/v1/edit/{doc_id}/pages/delete", json={"pages": [1]})
    assert r.status_code == 200
    assert r.json()["pageCount"] == 2

    r = client.post(f"/api/v1/edit/{doc_id}/pages/move", json={"from": 1, "to": 0})
    assert r.status_code == 200
    out = _download(client, doc_id)
    # original page 3 should now be first
    assert "PAGE-3" in out[0].get_text()
    out.close()


def test_delete_all_pages_rejected(client, sample_pdf):
    data = _open(client, sample_pdf)
    r = client.post(
        f"/api/v1/edit/{data['docId']}/pages/delete", json={"pages": [0, 1, 2]}
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


def test_open_invalid_pdf(client, tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf at all")
    r = client.post(
        "/api/v1/edit/open",
        files=[("fileInput", ("bad.pdf", bad.read_bytes(), "application/pdf"))],
    )
    assert r.status_code == 400
