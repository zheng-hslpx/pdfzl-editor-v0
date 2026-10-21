"""Tests for /api/v1/general/* page-operation endpoints."""
from __future__ import annotations

import asyncio
import io
import time
import zipfile
from unittest.mock import AsyncMock

import fitz
import pytest

from app.core.errors import PdfError
from app.core.pdf_engine import parse_page_ranges
from app.services import libreoffice

from conftest import make_sample_pdf, successful_job, submit_job


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
    assert r.json() == {"status": "ok"}


def test_readiness_returns_cached_state_without_waiting_for_probe(client, monkeypatch):
    calls = 0

    async def delayed_probe():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.25)
        return True

    monkeypatch.setattr(libreoffice, "soffice_available", lambda: True)
    monkeypatch.setattr(libreoffice, "_probe_cache", None)
    monkeypatch.setattr(libreoffice, "_probe_checked_at", 0.0)
    monkeypatch.setattr(libreoffice, "_probe_task", None)
    monkeypatch.setattr(libreoffice, "_probe_functional", delayed_probe)

    start = time.monotonic()
    first = client.get("/api/health/ready")
    elapsed = time.monotonic() - start
    assert first.status_code == 200
    assert elapsed < 0.2
    assert first.json()["status"] == "unknown"
    assert first.json()["checking"] is True

    again = client.get("/api/health/ready")
    assert again.json()["checking"] is True
    assert calls <= 1  # the probe is asynchronous and single-flight

    time.sleep(0.35)
    ready = client.get("/api/health/ready")
    assert ready.json()["status"] == "ready"
    assert ready.json()["checking"] is False
    assert calls == 1


def test_readiness_probe_timeout_is_inconclusive_and_retried(client, monkeypatch):
    calls = 0

    async def probe_sequence():
        nonlocal calls
        calls += 1
        return None if calls == 1 else True

    monkeypatch.setattr(libreoffice, "soffice_available", lambda: True)
    monkeypatch.setattr(libreoffice, "_probe_cache", None)
    monkeypatch.setattr(libreoffice, "_probe_checked_at", 0.0)
    monkeypatch.setattr(libreoffice, "_probe_checked_wall", None)
    monkeypatch.setattr(libreoffice, "_probe_task", None)
    monkeypatch.setattr(libreoffice, "_probe_functional", probe_sequence)
    monkeypatch.setattr(libreoffice.config, "LO_READY_RETRY_SECONDS", 3600.0)

    first = client.get("/api/health/ready")
    assert first.json()["status"] == "unknown"
    assert first.json()["checking"] is True

    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        state = client.get("/api/health/ready").json()
        if not state["checking"]:
            break
        time.sleep(0.01)
    assert state["status"] == "unknown"
    assert state["checking"] is False
    assert calls == 1

    monkeypatch.setattr(
        libreoffice, "_probe_checked_at", time.monotonic() - 3601.0
    )
    retry = client.get("/api/health/ready")
    assert retry.json()["status"] == "unknown"
    assert retry.json()["checking"] is True
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        state = client.get("/api/health/ready").json()
        if not state["checking"]:
            break
        time.sleep(0.01)
    assert state["status"] == "ready"
    assert state["checking"] is False
    assert calls == 2


def test_libreoffice_probe_timeout_returns_unknown(monkeypatch, tmp_path):
    class HangingProcess:
        pid = 12345
        returncode = None
        killed = False

        async def communicate(self):
            await asyncio.sleep(0.1)

        def kill(self):
            self.killed = True

        async def wait(self):
            self.returncode = -9

    class ProcessTree:
        def __init__(self, _memory_limit):
            pass

        def attach(self, _pid):
            pass

        def close(self):
            pass

    proc = HangingProcess()
    monkeypatch.setattr(libreoffice, "soffice_available", lambda: True)
    monkeypatch.setattr(libreoffice.config, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(libreoffice.config, "LO_READY_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(libreoffice, "WindowsJobObject", ProcessTree)
    monkeypatch.setattr(
        libreoffice.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=proc),
    )

    result = asyncio.run(libreoffice._probe_functional())

    assert result is None
    assert proc.killed is True
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------

def _upload(name: str, path) -> tuple:
    return ("fileInput", (name, path.read_bytes(), "application/pdf"))


def test_merge_two_pdfs(client, sample_pdf, tmp_path):
    second = make_sample_pdf(tmp_path / "second.pdf", pages=1)
    _, r = successful_job(client,
        "/api/v1/general/merge-pdfs",
        files=[_upload("a.pdf", sample_pdf), _upload("b.pdf", second)],
    )
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
    _, r = successful_job(client,
        "/api/v1/general/merge-pdfs",
        files=[_upload("a.pdf", sample_pdf), _upload("b.pdf", second)],
        data={"fileOrder": "b.pdf,a.pdf"},
    )
    out = _open(r.content)
    assert "SECOND FILE" in out[0].get_text()  # b.pdf's page first
    assert "PAGE-1" in out[1].get_text()  # then a.pdf
    out.close()


def test_merge_needs_two_files(client, sample_pdf):
    r = client.post(
        "/api/v1/general/merge-pdfs", files=[_upload("a.pdf", sample_pdf)],
        headers={"Idempotency-Key": "merge-one-file"},
    )
    assert r.status_code == 400


def test_merge_chinese_roundtrip(client, zh_pdf, sample_pdf):
    _, r = successful_job(client,
        "/api/v1/general/merge-pdfs",
        files=[_upload("zh.pdf", zh_pdf), _upload("a.pdf", sample_pdf)],
    )
    out = _open(r.content)
    assert "你好世界" in _full_text(out)
    out.close()


# ---------------------------------------------------------------------------
# split
# ---------------------------------------------------------------------------

def test_split_by_ranges(client, sample_pdf):
    _, r = successful_job(client,
        "/api/v1/general/split-pages",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"pageRanges": "1,2-3"},
    )
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(__import__("io").BytesIO(r.content)) as zf:
        names = zf.namelist()
        assert names == ["sample_pages_1.pdf", "sample_pages_2-3.pdf"]
        assert _open(zf.read(names[0])).page_count == 1
        assert _open(zf.read(names[1])).page_count == 2


def test_split_all(client, sample_pdf):
    _, r = successful_job(client,
        "/api/v1/general/split-pages",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"pageRanges": "all"},
    )
    with zipfile.ZipFile(__import__("io").BytesIO(r.content)) as zf:
        assert zf.namelist() == ["sample_pages_all.pdf"]
        assert _open(zf.read("sample_pages_all.pdf")).page_count == 3


@pytest.mark.parametrize("bad", ["", "5", "2-1", "abc"])
def test_split_invalid_ranges(client, sample_pdf, bad):
    result, _ = submit_job(client,
        "/api/v1/general/split-pages",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"pageRanges": bad},
    )
    assert result["state"] == "failed"
    assert result["error"]["code"] == "INVALID_INPUT"


# ---------------------------------------------------------------------------
# rotate / remove / reorder
# ---------------------------------------------------------------------------

def test_rotate(client, sample_pdf):
    _, r = successful_job(client,
        "/api/v1/general/rotate",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"angle": 90, "pages": "1"},
    )
    out = _open(r.content)
    assert out[0].rotation == 90
    assert out[1].rotation == 0
    out.close()


def test_rotate_bad_angle(client, sample_pdf):
    r = client.post(
        "/api/v1/general/rotate",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"angle": 45},
        headers={"Idempotency-Key": "rotate-bad-angle"},
    )
    assert r.status_code == 400


def test_remove_pages(client, sample_pdf):
    _, r = successful_job(client,
        "/api/v1/general/remove-pages",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"pages": "2"},
    )
    out = _open(r.content)
    assert out.page_count == 2
    text = _full_text(out)
    assert "PAGE-1" in text and "PAGE-3" in text and "PAGE-2" not in text
    out.close()


def test_remove_all_pages_rejected(client, sample_pdf):
    result, _ = submit_job(client,
        "/api/v1/general/remove-pages",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"pages": "all"},
    )
    assert result["state"] == "failed"
    assert result["error"]["code"] == "INVALID_INPUT"


def test_split_preserves_metadata_bookmarks_and_unique_names(client, tmp_path):
    source = fitz.open()
    for number in range(1, 5):
        page = source.new_page()
        page.insert_text((72, 72), f"SOURCE-PAGE-{number}")
    source.set_metadata({"title": "Split metadata test", "author": "PDF Editor"})
    source.set_toc([[1, "Second page bookmark", 2], [1, "Fourth page bookmark", 4]])
    input_path = tmp_path / "bookmark-source.pdf"
    source.save(input_path)
    source.close()

    _, response = successful_job(
        client,
        "/api/v1/general/split-pages",
        files=[_upload("bookmark-source.pdf", input_path)],
        data={"pageRanges": "2-3,4,4"},
    )

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert names == [
            "bookmark-source_pages_2-3.pdf",
            "bookmark-source_pages_4.pdf",
            "bookmark-source_pages_4_3.pdf",
        ]
        first = fitz.open(stream=archive.read(names[0]), filetype="pdf")
        assert first.page_count == 2
        assert first.metadata["title"] == "Split metadata test"
        assert first.get_toc() == [[1, "Second page bookmark", 1]]
        first.close()
        second = fitz.open(stream=archive.read(names[1]), filetype="pdf")
        assert second.page_count == 1
        assert second.get_toc() == [[1, "Fourth page bookmark", 1]]
        second.close()


def test_reorder_pages(client, sample_pdf):
    _, r = successful_job(client,
        "/api/v1/general/reorder-pages",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"pageOrder": "3,1,2"},
    )
    out = _open(r.content)
    assert "PAGE-3" in out[0].get_text()
    assert "PAGE-1" in out[1].get_text()
    assert "PAGE-2" in out[2].get_text()
    out.close()


def test_reorder_invalid_permutation(client, sample_pdf):
    result, _ = submit_job(client,
        "/api/v1/general/reorder-pages",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"pageOrder": "1,2"},
    )
    assert result["state"] == "failed"
    assert result["error"]["code"] == "INVALID_INPUT"


# ---------------------------------------------------------------------------
# temp file hygiene
# ---------------------------------------------------------------------------

def test_no_temp_residue(client, sample_pdf, temp_dir_path):
    before = set(temp_dir_path.glob("pdfeditor-*")) if temp_dir_path.exists() else set()
    _, _ = successful_job(client,
        "/api/v1/general/merge-pdfs",
        files=[_upload("a.pdf", sample_pdf), _upload("b.pdf", sample_pdf)],
    )
    after = set(temp_dir_path.glob("pdfeditor-*")) if temp_dir_path.exists() else set()
    assert after == before
