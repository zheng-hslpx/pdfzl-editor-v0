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
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .. import config


class TempFileManager:
    """Creates, registers and cleans up temporary files/directories."""

    def __init__(self, base_dir: Path, ttl_hours: float = 2.0):
        self.base_dir = base_dir
        self.ttl_hours = ttl_hours
        self._registry: dict[Path, float] = {}
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
        self._registry[path] = time.time()

    def unregister(self, path: Path) -> None:
        self._registry.pop(path, None)

    # -- cleanup ------------------------------------------------------
    def cleanup_old(self, max_age_hours: float | None = None) -> int:
        """Remove registered entries older than TTL. Returns count removed."""
        max_age = (max_age_hours if max_age_hours is not None else self.ttl_hours) * 3600
        now = time.time()
        removed = 0
        for path, created in list(self._registry.items()):
            if now - created > max_age:
                removed += 1 if self._delete(path) else 0
                self.unregister(path)
        return removed

    @staticmethod
    def _delete(path: Path) -> bool:
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists():
                path.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def discard(self, path: Path) -> None:
        """Explicitly delete a temp path (RAII close)."""
        self._delete(path)
        self.unregister(path)


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
    try:
        with open(dest, "wb") as fh:
            while chunk := await upload.read(1024 * 1024):
                fh.write(chunk)
    except BaseException:
        temp_manager.discard(dest)
        raise
    return dest
