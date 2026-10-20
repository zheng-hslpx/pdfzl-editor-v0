"""Temp file lifecycle, mirroring Stirling-PDF's TempFileManager / TempFile.

Patterns ported from Stirling-PDF (Java):
- TempFileManager.createTempFile / createTempDir + registry
- TempFile (RAII): close() deletes
- TempFileCleanupService scheduled cleanup of expired entries
- ManagedTempFileResource: response stream closed -> temp file deleted
  (Python equivalent: Starlette BackgroundTask on FileResponse)
"""
from __future__ import annotations

import shutil
import logging
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .. import config
from .errors import PdfError
from .input_validation import validate_pdf
from .input_validation import validate_pdf

logger = logging.getLogger("pdf_editor.temp")


class TempFileManager:
    """Creates, registers and cleans up temporary files/directories."""

    def __init__(self, base_dir: Path, ttl_hours: float = 2.0):
        self.base_dir = base_dir
        self.ttl_hours = ttl_hours
        self._registry: dict[Path, float] = {}
        self._lock = threading.RLock()
        base_dir.mkdir(parents=True, exist_ok=True)

    # -- creation -----------------------------------------------------
    def create_temp_file(self, suffix: str = ".pdf", prefix: str = "pdfeditor-") -> Path:
        path = self.base_dir / f"{prefix}{uuid.uuid4().hex}{suffix}"
        path.touch()
        self.register(path)
        return path

    def create_temp_dir(self, prefix: str = "pdfeditor-") -> Path:
        path = self.base_dir / f"{prefix}{uuid.uuid4().hex}"
        path.mkdir(parents=True, exist_ok=True)
        self.register(path)
        return path

    # -- registry -----------------------------------------------------
    def register(self, path: Path) -> None:
        with self._lock:
            self._registry[path] = time.time()

    def unregister(self, path: Path) -> None:
        with self._lock:
            self._registry.pop(path, None)

    # -- cleanup ------------------------------------------------------
    def cleanup_old(self, max_age_hours: float | None = None) -> int:
        """Remove registered entries older than TTL. Returns count removed."""
        max_age = (max_age_hours if max_age_hours is not None else self.ttl_hours) * 3600
        now = time.time()
        removed = 0
        with self._lock:
            entries = list(self._registry.items())
        for path, created in entries:
            if now - created > max_age:
                if self._delete(path):
                    removed += 1
                    self.unregister(path)
                else:
                    logger.warning("Could not delete expired temporary path: %s", path)
        return removed

    def cleanup_orphans(self) -> int:
        """Clean old unregistered top-level temp entries at process startup."""
        cutoff = time.time() - self.ttl_hours * 3600
        removed = 0
        with self._lock:
            registered = set(self._registry)
        try:
            children = list(self.base_dir.iterdir())
        except OSError:
            logger.exception("Could not scan temporary directory: %s", self.base_dir)
            return 0
        for path in children:
            if path.name == "sessions" or path in registered:
                continue
            try:
                if path.stat().st_mtime > cutoff:
                    continue
            except OSError:
                logger.warning("Could not stat temporary path: %s", path)
                continue
            if self._delete(path):
                removed += 1
            else:
                logger.warning("Could not delete orphan temporary path: %s", path)
        return removed

    @staticmethod
    def _delete(path: Path) -> bool:
        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def discard(self, path: Path) -> bool:
        """Explicitly delete a temp path (RAII close)."""
        deleted = self._delete(path)
        if deleted:
            self.unregister(path)
        else:
            logger.warning("Could not discard temporary path; retained for retry: %s", path)
        return deleted


temp_manager = TempFileManager(config.TEMP_DIR, config.TEMP_TTL_HOURS)


@contextmanager
def temp_file(suffix: str = ".pdf") -> Iterator[Path]:
    path = temp_manager.create_temp_file(suffix)
    try:
        yield path
    finally:
        temp_manager.discard(path)


@contextmanager
def temp_dir(prefix: str = "pdfeditor-") -> Iterator[Path]:
    path = temp_manager.create_temp_dir(prefix)
    try:
        yield path
    finally:
        temp_manager.discard(path)


async def save_upload(upload, suffix: str = ".pdf") -> Path:
    """Stream an UploadFile into a registered temp file (1 MiB chunks)."""
    dest = temp_manager.create_temp_file(suffix)
    size = 0
    try:
        with open(dest, "wb") as fh:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > config.MAX_UPLOAD_BYTES:
                    raise PdfError(
                        413,
                        f"单个文件超过 {config.MAX_UPLOAD_MB} MiB 上传上限",
                        code="UPLOAD_TOO_LARGE",
                    )
                fh.write(chunk)
        if suffix.lower() == ".pdf":
            validate_pdf(dest)
        if suffix.lower() == ".pdf":
            validate_pdf(dest)
    except BaseException:
        temp_manager.discard(dest)
        raise
    return dest
