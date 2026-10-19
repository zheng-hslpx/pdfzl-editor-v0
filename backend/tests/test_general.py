"""Tests for /api/v1/general/* page-operation endpoints."""
from __future__ import annotations

import zipfile

import fitz
import pytest

from app.core.errors import PdfError
from app.core.pdf_engine import parse_page_ranges

from conftest import make_sample_pdf


def _open(content: bytes) -> fitz.Document:
    return fitz.open(stream=content, filetype="pdf")


def _full_text(doc: fitz.Document) -> str:
    return "".join(doc[i].get_text() for i in range(doc.page_count))


# ---------------------------------------------------------------------------
# unit: page range parser
# ---------------------------------------------------------------------------

def test_parse_ranges_all():
    assert parse_page_ranges("all", 5) == [0, 1, 2, 3, 4]


def test_parse_ranges_mixed():
    assert parse_page_ranges("1,3,5-8", 10) == [0, 2, 4, 5, 6, 7]


def test_parse_ranges_invalid():
    with pytest.raises(PdfError):
        parse_page_ranges("", 10)
    with pytest.raises(PdfError):
        parse_page_ranges("11", 10)
    with pytest.raises(PdfError):
        parse_page_ranges("3-1", 10)
    with pytest.raises(PdfError):
        parse_page_ranges("abc", 10)


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------

def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------

def _upload(name: str, path) -> tuple:
    return ("fileInput", (name, path.read_bytes(), "application/pdf"))


def test_merge_two_pdfs(client, sample_pdf, tmp_path):
    second = make_sample_pdf(tmp_path / "second.pdf", pages=1)
    r = client.post(
        "/api/v1/general/merge-pdfs",
        files=[_upload("a.pdf", sample_pdf), _upload("b.pdf", second)],
    )
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    out = _open(r.content)
    assert out.page_count == 4
    assert "PAGE-1" in out[0].get_text()
    assert "PAGE-1" in out[3].get_text()  # second file's only page
    out.close()


def test_merge_with_file_order(client, sample_pdf, tmp_path):
    second = tmp_path / "second.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "SECOND FILE", fontsize=14)
    doc.save(str(second))
    doc.close()
    r = client.post(
        "/api/v1/general/merge-pdfs",
        files=[_upload("a.pdf", sample_pdf), _upload("b.pdf", second)],
        data={"fileOrder": "b.pdf,a.pdf"},
    )
    assert r.status_code == 200
    out = _open(r.content)
    assert "SECOND FILE" in out[0].get_text()  # b.pdf's page first
    assert "PAGE-1" in out[1].get_text()  # then a.pdf
    out.close()


def test_merge_needs_two_files(client, sample_pdf):
    r = client.post(
        "/api/v1/general/merge-pdfs", files=[_upload("a.pdf", sample_pdf)]
    )
    assert r.status_code == 400


def test_merge_chinese_roundtrip(client, zh_pdf, sample_pdf):
    r = client.post(
        "/api/v1/general/merge-pdfs",
        files=[_upload("zh.pdf", zh_pdf), _upload("a.pdf", sample_pdf)],
    )
    assert r.status_code == 200
    out = _open(r.content)
    assert "你好世界" in _full_text(out)
    out.close()


# ---------------------------------------------------------------------------
# split
# ---------------------------------------------------------------------------

def test_split_by_ranges(client, sample_pdf):
    r = client.post(
        "/api/v1/general/split-pages",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"pageRanges": "1,2-3"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(__import__("io").BytesIO(r.content)) as zf:
        names = zf.namelist()
        assert names == ["sample_pages_1.pdf", "sample_pages_2-3.pdf"]
        assert _open(zf.read(names[0])).page_count == 1
        assert _open(zf.read(names[1])).page_count == 2


def test_split_all(client, sample_pdf):
    r = client.post(
        "/api/v1/general/split-pages",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"pageRanges": "all"},
    )
    assert r.status_code == 200
    with zipfile.ZipFile(__import__("io").BytesIO(r.content)) as zf:
        assert zf.namelist() == ["sample_pages_all.pdf"]
        assert _open(zf.read("sample_pages_all.pdf")).page_count == 3


@pytest.mark.parametrize("bad", ["", "5", "2-1", "abc"])
def test_split_invalid_ranges(client, sample_pdf, bad):
    r = client.post(
        "/api/v1/general/split-pages",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"pageRanges": bad},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# rotate / remove / reorder
# ---------------------------------------------------------------------------

def test_rotate(client, sample_pdf):
    r = client.post(
        "/api/v1/general/rotate",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"angle": 90, "pages": "1"},
    )
    assert r.status_code == 200
    out = _open(r.content)
    assert out[0].rotation == 90
    assert out[1].rotation == 0
    out.close()


def test_rotate_bad_angle(client, sample_pdf):
    r = client.post(
        "/api/v1/general/rotate",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"angle": 45},
    )
    assert r.status_code == 400


def test_remove_pages(client, sample_pdf):
    r = client.post(
        "/api/v1/general/remove-pages",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"pages": "2"},
    )
    assert r.status_code == 200
    out = _open(r.content)
    assert out.page_count == 2
    text = _full_text(out)
    assert "PAGE-1" in text and "PAGE-3" in text and "PAGE-2" not in text
    out.close()


def test_remove_all_pages_rejected(client, sample_pdf):
    r = client.post(
        "/api/v1/general/remove-pages",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"pages": "all"},
    )
    assert r.status_code == 400


def test_reorder_pages(client, sample_pdf):
    r = client.post(
        "/api/v1/general/reorder-pages",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"pageOrder": "3,1,2"},
    )
    assert r.status_code == 200
    out = _open(r.content)
    assert "PAGE-3" in out[0].get_text()
    assert "PAGE-1" in out[1].get_text()
    assert "PAGE-2" in out[2].get_text()
    out.close()


def test_reorder_invalid_permutation(client, sample_pdf):
    r = client.post(
        "/api/v1/general/reorder-pages",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"pageOrder": "1,2"},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# temp file hygiene
# ---------------------------------------------------------------------------

def test_no_temp_residue(client, sample_pdf, temp_dir_path):
    before = set(temp_dir_path.iterdir()) if temp_dir_path.exists() else set()
    r = client.post(
        "/api/v1/general/merge-pdfs",
        files=[_upload("a.pdf", sample_pdf), _upload("b.pdf", sample_pdf)],
    )
    assert r.status_code == 200
    after = set(temp_dir_path.iterdir()) if temp_dir_path.exists() else set()
    assert after == before
