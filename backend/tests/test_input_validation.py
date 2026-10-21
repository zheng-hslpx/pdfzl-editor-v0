from __future__ import annotations

import fitz
import pytest

from app.core.errors import PdfError
from app.core.input_validation import validate_pdf
from app.core.pdf_engine import open_pdf
from conftest import submit_job


def test_png_content_is_rejected_by_validation_and_pdf_engine(sample_png):
    with pytest.raises(PdfError) as validation_error:
        validate_pdf(sample_png)
    assert validation_error.value.code == "INVALID_PDF"

    with pytest.raises(PdfError) as open_error:
        open_pdf(sample_png)
    assert open_error.value.code == "INVALID_PDF"


def test_edit_upload_rejects_png_disguised_as_pdf(client, sample_png):
    response = client.post(
        "/api/v1/edit/open",
        files=[("fileInput", ("fake.pdf", sample_png.read_bytes(), "application/pdf"))],
        headers={"Idempotency-Key": "png-disguised-as-pdf"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_PDF"
    assert "fake.pdf" not in response.text


def test_incrementally_updated_pdf_remains_uploadable(client, tmp_path):
    path = tmp_path / "incremental.pdf"
    source = fitz.open()
    source.new_page().insert_text((72, 100), "INITIAL PDF")
    source.save(path)
    source.close()

    updated = fitz.open(path)
    updated.set_metadata({"title": "Incremental update retained"})
    updated.saveIncr()
    updated.close()

    result, _ = submit_job(
        client,
        "/api/v1/edit/open",
        files=[("fileInput", ("incremental.pdf", path.read_bytes(), "application/pdf"))],
    )
    assert result["state"] == "succeeded", result.get("error")
    assert result["result"]["pageCount"] == 1


def test_pdf_endpoint_uses_content_when_filename_and_mime_disagree(client, sample_pdf):
    result, _ = submit_job(
        client,
        "/api/v1/edit/open",
        files=[("fileInput", ("document.png", sample_pdf.read_bytes(), "image/png"))],
    )
    assert result["state"] == "succeeded", result.get("error")
    assert result["result"]["pageCount"] == 3
