"""Application configuration (env-overridable)."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

# pdf-editor/ root: backend/app/config.py -> backend/app -> backend -> pdf-editor
BASE_DIR: Path = Path(__file__).resolve().parents[2]


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else default


TEMP_DIR: Path = _env_path("PDF_EDITOR_TEMP_DIR", BASE_DIR / "temp")
TEMP_TTL_HOURS: float = float(os.environ.get("PDF_EDITOR_TEMP_TTL_HOURS", "2"))
SESSION_TTL_HOURS: float = float(os.environ.get("PDF_EDITOR_SESSION_TTL_HOURS", "2"))

MAX_UPLOAD_MB: int = int(os.environ.get("PDF_EDITOR_MAX_UPLOAD_MB", "600"))
MAX_UPLOAD_BYTES: int = MAX_UPLOAD_MB * 1024 * 1024

SOFFICE_PATH: str = os.environ.get(
    "PDF_EDITOR_SOFFICE", r"C:\Program Files\LibreOffice\program\soffice.exe"
)
LO_TIMEOUT_SECONDS: int = int(os.environ.get("PDF_EDITOR_LO_TIMEOUT", "180"))

UI_ORIGIN: str = os.environ.get("PDF_EDITOR_UI_ORIGIN", "http://127.0.0.1:5173")
_ui_origin = urlsplit(UI_ORIGIN)
if (
    _ui_origin.scheme != "http"
    or _ui_origin.hostname != "127.0.0.1"
    or _ui_origin.path
    or _ui_origin.query
    or _ui_origin.fragment
    or _ui_origin.username
    or _ui_origin.password
):
    raise ValueError("PDF_EDITOR_UI_ORIGIN must be an http origin on 127.0.0.1")
CORS_ORIGINS: list[str] = [UI_ORIGIN]
LO_READY_TIMEOUT_SECONDS: float = float(
    os.environ.get("PDF_EDITOR_LO_READY_TIMEOUT_SECONDS", "10")
)
LO_READY_CACHE_SECONDS: float = float(
    os.environ.get("PDF_EDITOR_LO_READY_CACHE_SECONDS", "300")
)

JOB_TIMEOUT_SECONDS: float = float(os.environ.get("PDF_EDITOR_JOB_TIMEOUT_SECONDS", "900"))
JOB_MEMORY_LIMIT_BYTES: int = int(
    os.environ.get("PDF_EDITOR_JOB_MEMORY_MB", "2048")
) * 1024 * 1024
JOB_QUEUE_CAPACITY: int = 1
JOB_RESULT_TTL_HOURS: float = float(
    os.environ.get("PDF_EDITOR_JOB_RESULT_TTL_HOURS", "2")
)
PREVIEW_PAGE_PIXELS: int = 24_000_000
RENDER_BATCH_PIXELS: int = 160_000_000
EDITOR_HISTORY_MAX_STEPS: int = 10
EDITOR_HISTORY_MAX_BYTES: int = 1024 * 1024 * 1024


def ensure_dirs() -> None:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
