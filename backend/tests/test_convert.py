"""Tests for /api/v1/convert/* endpoints."""
from __future__ import annotations

import io
import time
import zipfile

import fitz
import pytest
from PIL import Image
from docx import Document

from app.services import libreoffice

from conftest import make_sample_pdf, make_sample_png, successful_job, submit_job


def _upload(name: str, path) -> tuple:
    return ("fileInput", (name, path.read_bytes(), "application/pdf"))


def test_pdf_to_text(client, sample_pdf):
    _, r = successful_job(client,
        "/api/v1/convert/pdf-to-text",
        files=[_upload("sample.pdf", sample_pdf)],
    )
    text = r.content.decode("utf-8")
    assert "PAGE-1 HELLO" in text
    assert "PAGE-3 HELLO" in text


def test_job_http_contract_and_idempotency(client, sample_pdf, tmp_path):
    key = "api-idempotency-test"
    files = [_upload("sample.pdf", sample_pdf)]
    first = client.post(
        "/api/v1/convert/pdf-to-text",
        files=files,
        headers={"Idempotency-Key": key},
    )
    assert first.status_code == 202
    assert set(first.json()) == {"jobId", "state"}
    job_id = first.json()["jobId"]

    duplicate = client.post(
        "/api/v1/convert/pdf-to-text",
        files=files,
        headers={"Idempotency-Key": key},
    )
    assert duplicate.status_code == 202
    assert duplicate.json()["jobId"] == job_id

    other_pdf = make_sample_pdf(tmp_path / "different.pdf", pages=1)
    conflict = client.post(
        "/api/v1/convert/pdf-to-text",
        files=[_upload("different.pdf", other_pdf)],
        headers={"Idempotency-Key": key},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_KEY_REUSED"

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status = client.get(f"/api/v1/jobs/{job_id}")
        assert status.status_code == 200
        if status.json()["state"] in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(0.025)
    result = status.json()
    assert result["state"] == "succeeded", result.get("error")
    download = client.get(result["resultUrl"])
    assert download.status_code == 200
    assert b"PAGE-1 HELLO" in download.content


def test_pdf_to_image_png_single_page(client, sample_pdf):
    _, r = successful_job(client,
        "/api/v1/convert/pdf-to-image",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"format": "png", "dpi": 72, "pages": "1"},
    )
    assert r.headers["content-type"] == "image/png"
    assert r.content[:4] == b"\x89PNG"
    img = fitz.Pixmap(io.BytesIO(r.content))
    assert (img.width, img.height) == (595, 842)
    img = None


def test_pdf_to_image_jpeg_dpi(client, sample_pdf):
    _, r = successful_job(client,
        "/api/v1/convert/pdf-to-image",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"format": "jpeg", "dpi": 144, "pages": "2"},
    )
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content[:2] == b"\xff\xd8"
    img = fitz.Pixmap(io.BytesIO(r.content))
    assert (img.width, img.height) == (1190, 1684)
    img = None


def test_pdf_to_image_multi_page_zip(client, sample_pdf):
    _, r = successful_job(client,
        "/api/v1/convert/pdf-to-image",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"format": "png", "dpi": 72},
    )
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert zf.namelist() == [
            "sample_page_1.png",
            "sample_page_2.png",
            "sample_page_3.png",
        ]


def test_pdf_to_image_bad_format(client, sample_pdf):
    r = client.post(
        "/api/v1/convert/pdf-to-image",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"format": "gif"},
        headers={"Idempotency-Key": "bad-format"},
    )
    assert r.status_code == 400


def test_pdf_to_word(client, sample_pdf):
    _, r = successful_job(client,
        "/api/v1/convert/pdf-to-word",
        files=[_upload("sample.pdf", sample_pdf)],
    )
    doc = Document(io.BytesIO(r.content))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    assert paragraphs, "docx should contain extracted text"
    joined = "\n".join(paragraphs)
    assert "HELLO" in joined


def test_image_to_pdf(client, sample_png):
    _, r = successful_job(client,
        "/api/v1/convert/image-to-pdf",
        files=[("fileInput", ("img.png", sample_png.read_bytes(), "image/png"))],
        data={"pageSize": "a4"},
    )
    out = fitz.open(stream=r.content, filetype="pdf")
    assert out.page_count == 1
    assert abs(out[0].rect.width - 595) < 2  # A4
    out.close()


def test_image_to_pdf_source_size(client, sample_png):
    _, r = successful_job(client,
        "/api/v1/convert/image-to-pdf",
        files=[("fileInput", ("img.png", sample_png.read_bytes(), "image/png"))],
        data={"pageSize": "source"},
    )
    out = fitz.open(stream=r.content, filetype="pdf")
    assert out.page_count == 1
    assert abs(out[0].rect.width - 90) < 0.2  # 96 DPI fallback
    assert abs(out[0].rect.height - 120) < 0.2
    out.close()


def test_image_to_pdf_uses_embedded_dpi(client, tmp_path):
    image_path = tmp_path / "dpi.png"
    Image.new("RGB", (120, 160), "white").save(image_path, dpi=(300, 300))
    _, response = successful_job(
        client,
        "/api/v1/convert/image-to-pdf",
        files=[("fileInput", ("dpi.png", image_path.read_bytes(), "image/png"))],
        data={"pageSize": "source"},
    )
    out = fitz.open(stream=response.content, filetype="pdf")
    assert abs(out[0].rect.width - 28.8) < 0.2
    assert abs(out[0].rect.height - 38.4) < 0.2
    out.close()


def test_pdf_to_image_300_pages_exceeds_cumulative_160mp(client, tmp_path):
    from app import config

    source = tmp_path / "three-hundred-pages.pdf"
    doc = fitz.open()
    for _ in range(300):
        doc.new_page(width=612, height=792)
    doc.save(source)
    doc.close()
    page_pixels = 680 * 880  # ceil(612/72*80) x ceil(792/72*80)
    assert page_pixels < config.RENDER_BATCH_PIXELS
    assert page_pixels * 300 > config.RENDER_BATCH_PIXELS

    _, response = successful_job(
        client,
        "/api/v1/convert/pdf-to-image",
        files=[_upload("three-hundred-pages.pdf", source)],
        data={"format": "png", "dpi": 80},
        timeout=90,
    )
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert len(archive.namelist()) == 300
        assert archive.namelist()[0] == "three-hundred-pages_page_1.png"
        assert archive.namelist()[-1] == "three-hundred-pages_page_300.png"


def test_liveness_stays_responsive_and_running_job_can_be_cancelled(client, tmp_path):
    source = tmp_path / "long-render.pdf"
    doc = fitz.open()
    for _ in range(300):
        doc.new_page(width=612, height=792)
    doc.save(source)
    doc.close()

    accepted = client.post(
        "/api/v1/convert/pdf-to-image",
        files=[_upload("long-render.pdf", source)],
        data={"format": "png", "dpi": 600, "pages": "all"},
        headers={"Idempotency-Key": "liveness-cancel-render"},
    )
    assert accepted.status_code == 202
    job_id = accepted.json()["jobId"]
    deadline = time.monotonic() + 10
    state = None
    while time.monotonic() < deadline:
        status = client.get(f"/api/v1/jobs/{job_id}")
        assert status.status_code == 200
        state = status.json()["state"]
        if state == "running" or state in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(0.025)
    assert state == "running"

    health_started = time.monotonic()
    health = client.get("/api/health")
    assert health.status_code == 200
    assert time.monotonic() - health_started < 2

    cancelled = client.post(f"/api/v1/jobs/{job_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"
    final = client.get(f"/api/v1/jobs/{job_id}").json()
    assert final["state"] == "cancelled"
    assert "resultUrl" not in final


def test_image_to_pdf_rejects_pdf(client, sample_pdf):
    r = client.post(
        "/api/v1/convert/image-to-pdf",
        files=[_upload("sample.pdf", sample_pdf)],
        headers={"Idempotency-Key": "bad-image-input"},
    )
    assert r.status_code == 400


@pytest.mark.skipif(
    not libreoffice.soffice_available(), reason="LibreOffice executable not available"
)
def test_office_to_pdf(client, sample_docx):
    _, r = successful_job(client,
        "/api/v1/convert/office-to-pdf",
        files=[("fileInput", ("sample.docx", sample_docx.read_bytes(),
                              "application/vnd.openxmlformats-officedocument"
                              ".wordprocessingml.document"))],
    )
    out = fitz.open(stream=r.content, filetype="pdf")
    assert out.page_count >= 1
    text = out[0].get_text()
    # Note: the heading "测试文档" extracts in reversed glyph order
    # (LibreOffice CJK heading font extraction artifact); the body text
    # below is the reliable Chinese-rendering assertion.
    assert "这是用于 LibreOffice 转换测试的中文内容。" in text
    out.close()


def test_office_to_pdf_bad_extension(client, sample_pdf):
    r = client.post(
        "/api/v1/convert/office-to-pdf",
        files=[_upload("sample.pdf", sample_pdf)],
        headers={"Idempotency-Key": "bad-office-extension"},
    )
    assert r.status_code == 400
