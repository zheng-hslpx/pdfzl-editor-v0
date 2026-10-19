"""Tests for /api/v1/misc/* endpoints."""
from __future__ import annotations

import fitz

from conftest import make_sample_pdf


def _upload(name: str, path) -> tuple:
    return ("fileInput", (name, path.read_bytes(), "application/pdf"))


def _open(content: bytes) -> fitz.Document:
    return fitz.open(stream=content, filetype="pdf")


def test_pdf_info(client, sample_pdf):
    r = client.post("/api/v1/misc/pdf-info", files=[_upload("sample.pdf", sample_pdf)])
    assert r.status_code == 200
    info = r.json()
    assert info["pageCount"] == 3
    assert abs(info["pageSize"]["width"] - 595) < 2
    assert isinstance(info["metadata"], dict)
    assert isinstance(info["fonts"], list)
    assert info["fileSize"] > 0


def test_compress(client, sample_pdf):
    r = client.post(
        "/api/v1/misc/compress",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"level": "medium"},
    )
    assert r.status_code == 200
    out = _open(r.content)
    assert out.page_count == 3
    out.close()


def test_compress_bad_level(client, sample_pdf):
    r = client.post(
        "/api/v1/misc/compress",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"level": "extreme"},
    )
    assert r.status_code == 400


def test_watermark(client, sample_pdf):
    r = client.post(
        "/api/v1/misc/watermark",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"text": "内部资料 CONFIDENTIAL", "opacity": 0.3, "fontSize": 48},
    )
    assert r.status_code == 200
    out = _open(r.content)
    text = out[0].get_text()
    assert "内部资料" in text or "CONFIDENTIAL" in text
    out.close()


def test_watermark_empty_text(client, sample_pdf):
    r = client.post(
        "/api/v1/misc/watermark",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"text": "  "},
    )
    assert r.status_code == 400


def test_add_page_numbers(client, sample_pdf):
    r = client.post(
        "/api/v1/misc/add-page-numbers",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"position": "bottom-center", "startNumber": 7},
    )
    assert r.status_code == 200
    out = _open(r.content)
    assert out.page_count == 3
    assert "7" in out[0].get_text()
    assert "8" in out[1].get_text()
    assert "9" in out[2].get_text()
    out.close()


def test_add_page_numbers_bad_position(client, sample_pdf):
    r = client.post(
        "/api/v1/misc/add-page-numbers",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"position": "middle"},
    )
    assert r.status_code == 400


def test_merge_output_from_samples(client, tmp_path):
    """End-to-end sanity: two generated docs merge into 5 pages."""
    a = make_sample_pdf(tmp_path / "a.pdf", pages=3)
    b = make_sample_pdf(tmp_path / "b.pdf", pages=2)
    r = client.post(
        "/api/v1/general/merge-pdfs",
        files=[_upload("a.pdf", a), _upload("b.pdf", b)],
    )
    assert r.status_code == 200
    out = _open(r.content)
    assert out.page_count == 5
    out.close()
