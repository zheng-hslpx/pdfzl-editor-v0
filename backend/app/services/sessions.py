"""Edit sessions: docId -> working PDF copy with TTL lifecycle.

Simplified from Stirling-PDF's jobId-based incremental editing: every
mutation is applied to the session file atomically (tmp + os.replace),
so no long-lived document handles are held.
"""
from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

from .. import config
from ..core.errors import PdfError
from ..core.tempfile import temp_manager


class EditSession:
    def __init__(self, doc_id: str, path: Path, original_name: str):
        self.id = doc_id
        self.path = path
        self.original_name = original_name
        self.created_at = time.time()

    def touch(self) -> None:
        self.created_at = time.time()


class SessionManager:
    def __init__(self, base_dir: Path, ttl_hours: float = 2.0):
        self.base_dir = base_dir
        self.ttl_hours = ttl_hours
        self._sessions: dict[str, EditSession] = {}
        base_dir.mkdir(parents=True, exist_ok=True)

    def create(self, src: Path, original_name: str) -> EditSession:
        doc_id = uuid.uuid4().hex
        session_dir = self.base_dir / doc_id
        session_dir.mkdir(parents=True, exist_ok=True)
        dest = session_dir / "document.pdf"
        shutil.copy2(src, dest)
        temp_manager.register(session_dir)
        session = EditSession(doc_id, dest, original_name)
        self._sessions[doc_id] = session
        return session

    def get(self, doc_id: str) -> EditSession:
        session = self._sessions.get(doc_id)
        if session is None:
            raise PdfError(404, "编辑会话不存在或已过期，请重新打开文件")
        session.touch()
        return session

    def close(self, doc_id: str) -> None:
        session = self._sessions.pop(doc_id, None)
        if session is not None:
            temp_manager.discard(session.path.parent)

    def cleanup_expired(self) -> int:
        now = time.time()
        expired = [
            sid
            for sid, s in self._sessions.items()
            if now - s.created_at > self.ttl_hours * 3600
        ]
        for sid in expired:
            self.close(sid)
        return len(expired)


session_manager = SessionManager(
    config.TEMP_DIR / "sessions", config.SESSION_TTL_HOURS
)
