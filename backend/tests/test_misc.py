"""Tests for /api/v1/misc/* endpoints."""
from __future__ import annotations

import fitz
import pytest

from app.services.pdf_worker import _encryption_status

from conftest import make_sample_pdf, successful_job


def _upload(name: str, path) -> tuple:
    return ("fileInput", (name, path.read_bytes(), "application/pdf"))


def _open(content: bytes) -> fitz.Document:
    return fitz.open(stream=content, filetype="pdf")


def test_pdf_info(client, sample_pdf):
    job, _ = successful_job(client, "/api/v1/misc/pdf-info", files=[_upload("sample.pdf", sample_pdf)])
    info = job["result"]
    assert info["pageCount"] == 3
    assert abs(info["pageSize"]["width"] - 595) < 2
    assert isinstance(info["metadata"], dict)
    assert isinstance(info["fonts"], list)
    assert info["fileSize"] > 0


def test_pdf_info_reports_encrypted_state_and_unknown_value(client, sample_pdf, tmp_path):
    encrypted_path = tmp_path / "encrypted.pdf"
    source = fitz.open(sample_pdf)
    source.save(
        encrypted_path,
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner-password",
        user_pw="user-password",
    )
    source.close()
    job, _ = successful_job(
        client,
        "/api/v1/misc/pdf-info",
        files=[_upload("encrypted.pdf", encrypted_path)],
    )
    assert job["result"]["isEncrypted"] is True

    class UnknownEncryptionState:
        @property
        def is_encrypted(self):
            raise RuntimeError("MuPDF could not determine encryption state")

    assert _encryption_status(UnknownEncryptionState()) is None


def test_compress(client, sample_pdf):
    _, r = successful_job(client,
        "/api/v1/misc/compress",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"level": "medium"},
    )
    out = _open(r.content)
    assert out.page_count == 3
    out.close()


def test_compress_bad_level(client, sample_pdf):
    r = client.post(
        "/api/v1/misc/compress",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"level": "extreme"},
        headers={"Idempotency-Key": "compress-bad-level"},
    )
    assert r.status_code == 400


def test_watermark(client, sample_pdf):
    _, r = successful_job(client,
        "/api/v1/misc/watermark",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"text": "内部资料 CONFIDENTIAL", "opacity": 0.3, "fontSize": 48},
    )
    out = _open(r.content)
    text = out[0].get_text()
    assert "内部资料" in text or "CONFIDENTIAL" in text
    out.close()


def test_watermark_empty_text(client, sample_pdf):
    r = client.post(
        "/api/v1/misc/watermark",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"text": "  "},
        headers={"Idempotency-Key": "watermark-empty"},
    )
    assert r.status_code == 400


def test_add_page_numbers(client, sample_pdf):
    _, r = successful_job(client,
        "/api/v1/misc/add-page-numbers",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"position": "bottom-center", "startNumber": 7},
    )
    out = _open(r.content)
    assert out.page_count == 3
    assert "7" in out[0].get_text()
    assert "8" in out[1].get_text()
    assert "9" in out[2].get_text()
    out.close()


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_page_numbers_follow_rotated_display_coordinates(client, tmp_path, rotation):
    source_path = tmp_path / f"rotated-{rotation}.pdf"
    source = fitz.open()
    page = source.new_page(width=612, height=792)
    page.set_rotation(rotation)
    source.save(source_path)
    source.close()

    _, response = successful_job(
        client,
        "/api/v1/misc/add-page-numbers",
        files=[_upload(source_path.name, source_path)],
        data={"position": "bottom-right", "startNumber": 27, "margin": 36},
    )
    out = _open(response.content)
    page = out[0]
    line = next(
        line
        for block in page.get_text("dict")["blocks"]
        if block.get("type") == 0
        for line in block["lines"]
        if "27" in "".join(span["text"] for span in line["spans"])
    )
    display_bbox = fitz.Rect(line["bbox"]) * page.rotation_matrix
    assert display_bbox.height > display_bbox.width
    assert display_bbox.x1 > page.rect.width - 180
    assert display_bbox.y1 > page.rect.height - 100
    out.close()


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_watermark_uses_rotated_display_center(client, tmp_path, rotation):
    source_path = tmp_path / f"watermark-rotated-{rotation}.pdf"
    source = fitz.open()
    page = source.new_page(width=612, height=792)
    page.set_rotation(rotation)
    source.save(source_path)
    source.close()

    _, response = successful_job(
        client,
        "/api/v1/misc/watermark",
        files=[_upload(source_path.name, source_path)],
        data={"text": "VISIBLE WATERMARK", "fontSize": 24, "rotation": 30},
    )
    out = _open(response.content)
    page = out[0]
    text = page.get_text()
    assert "VISIBLEWATERMARK" in "".join(text.split())
    lines = [
        line
        for block in page.get_text("dict")["blocks"]
        if block.get("type") == 0
        for line in block["lines"]
    ]
    assert lines
    display_bbox = fitz.Rect(lines[0]["bbox"]) * page.rotation_matrix
    for line in lines[1:]:
        display_bbox |= fitz.Rect(line["bbox"]) * page.rotation_matrix
    assert page.rect.contains(display_bbox)
    assert abs((display_bbox.x0 + display_bbox.x1) / 2 - page.rect.width / 2) < 25
    assert abs((display_bbox.y0 + display_bbox.y1) / 2 - page.rect.height / 2) < 25
    direction = lines[0]["dir"]
    origin = fitz.Point(0, 0) * page.rotation_matrix
    endpoint = fitz.Point(*direction) * page.rotation_matrix
    display_direction = (endpoint.x - origin.x, endpoint.y - origin.y)
    expected = (3 ** 0.5 / 2, -0.5)  # same display angle as an unrotated page
    assert abs(display_direction[0] - expected[0]) < 0.05
    assert abs(display_direction[1] - expected[1]) < 0.05
    out.close()


def test_add_page_numbers_bad_position(client, sample_pdf):
    r = client.post(
        "/api/v1/misc/add-page-numbers",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"position": "middle"},
        headers={"Idempotency-Key": "page-number-bad-position"},
    )
    assert r.status_code == 400


def test_merge_output_from_samples(client, tmp_path):
    """End-to-end sanity: two generated docs merge into 5 pages."""
    a = make_sample_pdf(tmp_path / "a.pdf", pages=3)
    b = make_sample_pdf(tmp_path / "b.pdf", pages=2)
    _, r = successful_job(client,
        "/api/v1/general/merge-pdfs",
        files=[_upload("a.pdf", a), _upload("b.pdf", b)],
    )
    out = _open(r.content)
    assert out.page_count == 5
    out.close()
