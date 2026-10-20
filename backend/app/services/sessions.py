"""Edit sessions: docId -> working PDF copy with TTL lifecycle.

Simplified from Stirling-PDF's jobId-based incremental editing: every
mutation is applied to the session file atomically (tmp + os.replace),
so no long-lived document handles are held.
"""
from __future__ import annotations

import shutil
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .. import config
from ..core.errors import PdfError

logger = logging.getLogger("pdf_editor.sessions")


@dataclass
class SessionHistoryEntry:
    id: str
    kind: Literal["snapshot", "inverse"]
    size_bytes: int = 0
    snapshot_path: Path | None = None
    page_ids: list[str] | None = None
    page_metadata: list[dict] | None = None
    undo_action: dict | None = None
    redo_action: dict | None = None


class EditSession:
    def __init__(self, doc_id: str, path: Path, original_name: str, page_count: int):
        self.id = doc_id
        self.path = path
        self.original_name = original_name
        self.created_at = time.time()
        self.last_active_at = self.created_at
        self.revision = 0
        self.page_ids = [uuid.uuid4().hex for _ in range(page_count)]
        self.page_metadata: list[dict] = []
        self.lock = threading.RLock()
        self.pending_job_id: str | None = None
        self.closed = False
        self.undo_stack: list[SessionHistoryEntry] = []
        self.redo_stack: list[SessionHistoryEntry] = []

    def touch(self) -> None:
        self.last_active_at = time.time()
        try:
            os.utime(self.path.parent, None)
        except OSError:
            logger.warning("Could not refresh session directory activity time: %s", self.path.parent)


class SessionManager:
    def __init__(self, base_dir: Path, ttl_hours: float = 2.0):
        self.base_dir = base_dir
        self.ttl_hours = ttl_hours
        self._sessions: dict[str, EditSession] = {}
        self._orphan_dirs: set[Path] = set()
        base_dir.mkdir(parents=True, exist_ok=True)

    def create(self, src: Path, original_name: str, page_count: int) -> EditSession:
        doc_id = uuid.uuid4().hex
        session_dir = self.base_dir / doc_id
        session_dir.mkdir(parents=True, exist_ok=True)
        dest = session_dir / "document.pdf"
        try:
            # Upload temp files and sessions live under TEMP_DIR on the same
            # volume; transfer ownership atomically instead of blocking the
            # API loop while duplicating a potentially 600 MiB file.
            if Path(src).resolve().drive.lower() == dest.resolve().drive.lower():
                os.replace(src, dest)
            else:
                shutil.copy2(src, dest)
        except BaseException:
            if not self._delete_dir(session_dir):
                self._orphan_dirs.add(session_dir)
            raise
        session = EditSession(doc_id, dest, original_name, page_count)
        self._sessions[doc_id] = session
        return session

    def get(self, doc_id: str) -> EditSession:
        session = self._sessions.get(doc_id)
        if session is None or session.closed:
            raise PdfError(404, "编辑会话不存在或已过期，请重新打开文件")
        session.touch()
        return session

    def touch(self, doc_id: str) -> None:
        self.get(doc_id)

    def close(self, doc_id: str) -> None:
        session = self._sessions.get(doc_id)
        if session is None:
            return
        with session.lock:
            if self._sessions.get(doc_id) is not session:
                return
            session.closed = True
            self._sessions.pop(doc_id, None)
            if not self._delete_dir(session.path.parent):
                self._orphan_dirs.add(session.path.parent)

    def cleanup_expired(self) -> int:
        now = time.time()
        expired = [
            sid
            for sid, s in self._sessions.items()
            if now - s.last_active_at > self.ttl_hours * 3600
        ]
        for sid in expired:
            self.close(sid)
        return len(expired)

    def cleanup_orphans(self) -> int:
        """Remove session directories left by a previous single-worker run."""
        removed = 0
        cutoff = time.time() - self.ttl_hours * 3600
        try:
            children = list(self.base_dir.iterdir())
        except OSError:
            logger.exception("Could not scan session directory: %s", self.base_dir)
            return 0
        active = {session.path.parent for session in self._sessions.values()}
        for path in children:
            if path in active or not path.is_dir():
                continue
            if path not in self._orphan_dirs:
                try:
                    if path.stat().st_mtime > cutoff:
                        continue
                except OSError:
                    logger.warning("Could not stat orphan session directory: %s", path)
                    continue
            if self._delete_dir(path):
                removed += 1
                self._orphan_dirs.discard(path)
            else:
                self._orphan_dirs.add(path)
        return removed

    def close_all(self) -> int:
        ids = list(self._sessions)
        for sid in ids:
            self.close(sid)
        return len(ids)

    @staticmethod
    def _delete_dir(path: Path) -> bool:
        try:
            if path.exists():
                shutil.rmtree(path)
            return True
        except OSError:
            logger.exception("Could not delete edit session directory: %s", path)
            return False


session_manager = SessionManager(
    config.TEMP_DIR / "sessions", config.SESSION_TTL_HOURS
)
