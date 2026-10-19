"""Shared pytest fixtures: sample PDFs (latin + Chinese), docx, png."""
from __future__ import annotations

from pathlib import Path

import fitz
import pytest
from docx import Document
from PIL import Image


def make_sample_pdf(path: Path, pages: int = 3) -> Path:
    """3-page PDF, each page carrying 'PAGE-{n} HELLO WORLD'."""
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), f"PAGE-{i + 1} HELLO WORLD", fontsize=14)
    doc.save(str(path))
    doc.close()
    return path


def make_zh_pdf(path: Path) -> Path:
    """1-page PDF with Chinese text (via built-in china-s font)."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "你好世界 HELLO", fontsize=14, fontname="china-s")
    doc.save(str(path))
    doc.close()
    return path


def make_sample_docx(path: Path) -> Path:
    doc = Document()
    doc.add_heading("测试文档", 0)
    doc.add_paragraph("这是用于 LibreOffice 转换测试的中文内容。")
    doc.save(str(path))
    return path


def make_sample_png(path: Path, size: tuple[int, int] = (120, 160)) -> Path:
    Image.new("RGB", size, (200, 60, 60)).save(str(path))
    return path


@pytest.fixture()
def sample_pdf(tmp_path: Path) -> Path:
    return make_sample_pdf(tmp_path / "sample.pdf")


@pytest.fixture()
def zh_pdf(tmp_path: Path) -> Path:
    return make_zh_pdf(tmp_path / "zh.pdf")


@pytest.fixture()
def sample_docx(tmp_path: Path) -> Path:
    return make_sample_docx(tmp_path / "sample.docx")


@pytest.fixture()
def sample_png(tmp_path: Path) -> Path:
    return make_sample_png(tmp_path / "sample.png")


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def temp_dir_path():
    from app import config

    return config.TEMP_DIR
