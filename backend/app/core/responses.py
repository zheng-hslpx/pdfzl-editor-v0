"""Download responses: Content-Disposition (RFC 5987) + delete-after-send.

Ports Stirling-PDF's WebResponseUtils.encodeAttachmentName behavior:
non-ASCII filenames carry both an ASCII fallback and a filename*=utf-8''...
parameter so Chinese names survive browser round-trips.
"""
from __future__ import annotations

import shutil
import re
from pathlib import Path
from urllib.parse import quote

from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .tempfile import temp_manager


def _ascii_fallback(filename: str) -> str:
    safe = _safe_filename(filename)
    return safe.encode("ascii", "replace").decode("ascii").replace('"', "'")


def _safe_filename(filename: str) -> str:
    """Return a basename safe for both RFC 6266 parameters and Windows."""
    value = str(filename).replace("\\", "/").split("/")[-1]
    value = re.sub(r"[\x00-\x1f\x7f]", "", value).strip().strip(".")
    return value or "download.pdf"


def content_disposition(filename: str, inline: bool = False) -> str:
    disposition = "inline" if inline else "attachment"
    filename = _safe_filename(filename)
    fallback = _ascii_fallback(filename)
    try:
        filename.encode("ascii")
    except UnicodeEncodeError:
        encoded = quote(filename, safe="")
        return f"{disposition}; filename=\"{fallback}\"; filename*=utf-8''{encoded}"
    return f'{disposition}; filename="{fallback}"'


def _safe_unlink(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink(missing_ok=True)
    except OSError:
        pass


def file_response(
    path: Path,
    filename: str,
    media_type: str = "application/pdf",
    delete_after: bool = True,
    inline: bool = False,
) -> FileResponse:
    """Stream a file to the client, deleting it after the response is sent."""
    headers = {"Content-Disposition": content_disposition(filename, inline)}
    # temp_manager.discard deletes AND unregisters (no registry leak).
    background = BackgroundTask(temp_manager.discard, path) if delete_after else None
    return FileResponse(
        path=str(path),
        media_type=media_type,
        headers=headers,
        background=background,
    )
