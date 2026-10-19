"""Tests for /api/v1/convert/* endpoints."""
from __future__ import annotations

import io
import zipfile

import fitz
import pytest
from docx import Document

from app.services import libreoffice

from conftest import make_sample_pdf, make_sample_png


def _upload(name: str, path) -> tuple:
    return ("fileInput", (name, path.read_bytes(), "application/pdf"))


def test_pdf_to_text(client, sample_pdf):
    r = client.post(
        "/api/v1/convert/pdf-to-text",
        files=[_upload("sample.pdf", sample_pdf)],
    )
    assert r.status_code == 200
    text = r.content.decode("utf-8")
    assert "PAGE-1 HELLO" in text
    assert "PAGE-3 HELLO" in text


def test_pdf_to_image_png_single_page(client, sample_pdf):
    r = client.post(
        "/api/v1/convert/pdf-to-image",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"format": "png", "dpi": 72, "pages": "1"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:4] == b"\x89PNG"
    img = fitz.Pixmap(io.BytesIO(r.content))
    assert (img.width, img.height) == (595, 842)
    img = None


def test_pdf_to_image_jpeg_dpi(client, sample_pdf):
    r = client.post(
        "/api/v1/convert/pdf-to-image",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"format": "jpeg", "dpi": 144, "pages": "2"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content[:2] == b"\xff\xd8"
    img = fitz.Pixmap(io.BytesIO(r.content))
    assert (img.width, img.height) == (1190, 1684)
    img = None


def test_pdf_to_image_multi_page_zip(client, sample_pdf):
    r = client.post(
        "/api/v1/convert/pdf-to-image",
        files=[_upload("sample.pdf", sample_pdf)],
        data={"format": "png", "dpi": 72},
    )
    assert r.status_code == 200
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
    )
    assert r.status_code == 400


def test_pdf_to_word(client, sample_pdf):
    r = client.post(
        "/api/v1/convert/pdf-to-word",
        files=[_upload("sample.pdf", sample_pdf)],
    )
    assert r.status_code == 200
    doc = Document(io.BytesIO(r.content))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    assert paragraphs, "docx should contain extracted text"
    joined = "\n".join(paragraphs)
    assert "HELLO" in joined


def test_image_to_pdf(client, sample_png):
    r = client.post(
        "/api/v1/convert/image-to-pdf",
        files=[("fileInput", ("img.png", sample_png.read_bytes(), "image/png"))],
        data={"pageSize": "a4"},
    )
    assert r.status_code == 200
    out = fitz.open(stream=r.content, filetype="pdf")
    assert out.page_count == 1
    assert abs(out[0].rect.width - 595) < 2  # A4
    out.close()


def test_image_to_pdf_source_size(client, sample_png):
    r = client.post(
        "/api/v1/convert/image-to-pdf",
        files=[("fileInput", ("img.png", sample_png.read_bytes(), "image/png"))],
        data={"pageSize": "source"},
    )
    assert r.status_code == 200
    out = fitz.open(stream=r.content, filetype="pdf")
    assert out.page_count == 1
    assert (out[0].rect.width, out[0].rect.height) == (120, 160)
    out.close()


def test_image_to_pdf_rejects_pdf(client, sample_pdf):
    r = client.post(
        "/api/v1/convert/image-to-pdf",
        files=[_upload("sample.pdf", sample_pdf)],
    )
    assert r.status_code == 400


@pytest.mark.skipif(
    not libreoffice.soffice_functional(), reason="LibreOffice not functional"
)
def test_office_to_pdf(client, sample_docx):
    r = client.post(
        "/api/v1/convert/office-to-pdf",
        files=[("fileInput", ("sample.docx", sample_docx.read_bytes(),
                              "application/vnd.openxmlformats-officedocument"
                              ".wordprocessingml.document"))],
    )
    assert r.status_code == 200
    out = fitz.open(stream=r.content, filetype="pdf")
    assert out.page_count >= 1
    text = out[0].get_text()
    assert "测试文档" in text
    out.close()


def test_office_to_pdf_bad_extension(client, sample_pdf):
    r = client.post(
        "/api/v1/convert/office-to-pdf",
        files=[_upload("sample.pdf", sample_pdf)],
    )
    assert r.status_code == 400
