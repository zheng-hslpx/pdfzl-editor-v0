"""Application configuration (env-overridable)."""
from __future__ import annotations

import os
from pathlib import Path

# pdf-editor/ root: backend/app/config.py -> backend/app -> backend -> pdf-editor
BASE_DIR: Path = Path(__file__).resolve().parents[2]


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else default


TEMP_DIR: Path = _env_path("PDF_EDITOR_TEMP_DIR", BASE_DIR / "temp")
TEMP_TTL_HOURS: float = float(os.environ.get("PDF_EDITOR_TEMP_TTL_HOURS", "2"))
SESSION_TTL_HOURS: float = float(os.environ.get("PDF_EDITOR_SESSION_TTL_HOURS", "2"))

MAX_UPLOAD_MB: int = int(os.environ.get("PDF_EDITOR_MAX_UPLOAD_MB", "300"))
MAX_UPLOAD_BYTES: int = MAX_UPLOAD_MB * 1024 * 1024

SOFFICE_PATH: str = os.environ.get(
    "PDF_EDITOR_SOFFICE", r"C:\Program Files\LibreOffice\program\soffice.exe"
)
LO_TIMEOUT_SECONDS: int = int(os.environ.get("PDF_EDITOR_LO_TIMEOUT", "180"))

CORS_ORIGINS: list[str] = [
    o.strip()
    for o in os.environ.get(
        "PDF_EDITOR_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if o.strip()
]


def ensure_dirs() -> None:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
