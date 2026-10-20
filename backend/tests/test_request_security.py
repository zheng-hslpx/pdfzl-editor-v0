from __future__ import annotations

import asyncio
import os
import time

import pytest

from app import config
from app.core.request_middleware import RequestBodyLimitMiddleware
from app.core.responses import content_disposition
from app.core.tempfile import TempFileManager, save_upload, temp_manager
from app.services.sessions import SessionManager


class ChunkUpload:
    def __init__(self, chunks: list[bytes]):
        self.chunks = list(chunks)

    async def read(self, _size: int) -> bytes:
        return self.chunks.pop(0) if self.chunks else b""


def test_request_body_limit_counts_actual_multipart_bytes():
    async def scenario():
        events = []

        async def app(scope, receive, send):
            while True:
                message = await receive()
                if message["type"] == "http.disconnect" or not message.get("more_body", False):
                    break
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        middleware = RequestBodyLimitMiddleware(app, max_bytes=10)
        multipart = (
            b"--part\r\nContent-Disposition: form-data; name=\"fileInput\"; filename=\"a.pdf\"\r\n\r\na"
            b"\r\n--part\r\nContent-Disposition: form-data; name=\"fileInput\"; filename=\"b.pdf\"\r\n\r\nb"
            b"\r\n--part\r\nContent-Disposition: form-data; name=\"pageRanges\"\r\n\r\nall"
            b"\r\n--part--\r\n"
        )
        messages = [
            {"type": "http.request", "body": multipart[:8], "more_body": True},
            {"type": "http.request", "body": multipart[8:32], "more_body": True},
            {"type": "http.request", "body": multipart[32:], "more_body": False},
        ]

        async def receive():
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        async def send(event):
            events.append(event)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/upload",
            "headers": [(b"content-type", b"multipart/form-data; boundary=part"),
                        (b"content-length", b"1")],
        }
        await middleware(scope, receive, send)
        return events

    events = asyncio.run(scenario())
    start = next(e for e in events if e["type"] == "http.response.start")
    body = next(e for e in events if e["type"] == "http.response.body")["body"]
    assert start["status"] == 413
    assert b"UPLOAD_TOO_LARGE" in body
    assert config.MAX_UPLOAD_BYTES == 600 * 1024 * 1024


def test_request_body_length_header_fast_rejects():
    async def scenario():
        called = False
        events = []

        async def app(scope, receive, send):
            nonlocal called
            called = True

        async def receive():
            return {"type": "http.disconnect"}

        async def send(event):
            events.append(event)

        middleware = RequestBodyLimitMiddleware(app, max_bytes=10)
        await middleware(
            {"type": "http", "method": "POST", "path": "/upload",
             "headers": [(b"content-type", b"multipart/form-data; boundary=x"),
                         (b"content-length", b"11")]},
            receive,
            send,
        )
        return called, events

    called, events = asyncio.run(scenario())
    assert not called
    assert events[0]["status"] == 413


def test_multipart_parser_closes_spooled_upload_on_stream_overflow(monkeypatch):
    from fastapi import FastAPI, File, UploadFile
    import starlette.formparsers

    opened = []
    real_spooled_file = starlette.formparsers.SpooledTemporaryFile

    class TrackedSpool:
        def __init__(self, *args, **kwargs):
            self.inner = real_spooled_file(*args, **kwargs)
            self.closed = False
            opened.append(self)

        def close(self):
            self.closed = True
            self.inner.close()

        def __getattr__(self, name):
            return getattr(self.inner, name)

    monkeypatch.setattr(starlette.formparsers, "SpooledTemporaryFile", TrackedSpool)
    app = FastAPI()

    @app.post("/upload")
    async def upload(file: UploadFile = File(...)):
        await file.close()
        return {"ok": True}

    boundary = b"test-boundary"
    prefix = (
        b"--" + boundary
        + b"\r\nContent-Disposition: form-data; name=\"file\"; filename=\"x.bin\""
        + b"\r\nContent-Type: application/octet-stream\r\n\r\n"
    )
    body_chunks = [prefix, b"x" * 128, b"\r\n--" + boundary + b"--\r\n"]
    limit = len(prefix) + 8
    events = []

    async def receive():
        if body_chunks:
            chunk = body_chunks.pop(0)
            return {"type": "http.request", "body": chunk, "more_body": bool(body_chunks)}
        return {"type": "http.disconnect"}

    async def send(event):
        events.append(event)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/upload",
        "raw_path": b"/upload",
        "query_string": b"",
        "headers": [
            (b"content-type", b"multipart/form-data; boundary=test-boundary"),
            (b"content-length", b"1"),
            (b"host", b"127.0.0.1"),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
    }
    asyncio.run(RequestBodyLimitMiddleware(app, limit)(scope, receive, send))
    response_start = next(event for event in events if event["type"] == "http.response.start")
    assert response_start["status"] == 413
    assert opened and opened[0].closed


def test_save_upload_enforces_actual_file_bytes_and_cleans(monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 5)
    created = []
    original_create = temp_manager.create_temp_file

    def capture_create(suffix=".pdf", prefix="pdfeditor-"):
        path = original_create(suffix, prefix)
        created.append(path)
        return path

    monkeypatch.setattr(temp_manager, "create_temp_file", capture_create)
    with pytest.raises(Exception) as exc:
        asyncio.run(save_upload(ChunkUpload([b"123", b"456"])))
    assert getattr(exc.value, "status_code", None) == 413
    assert created and not created[-1].exists()
    assert created[-1] not in temp_manager._registry


def test_temp_delete_failure_stays_registered(tmp_path, monkeypatch):
    manager = TempFileManager(tmp_path / "temp", ttl_hours=1)
    path = manager.create_temp_file()
    monkeypatch.setattr(manager, "_delete", lambda _path: False)
    assert not manager.discard(path)
    assert path in manager._registry


def test_session_idle_ttl_uses_last_activity(sample_pdf, tmp_path, monkeypatch):
    import app.services.sessions as sessions_module

    now = [10_000.0]
    monkeypatch.setattr(sessions_module.time, "time", lambda: now[0])
    manager = SessionManager(tmp_path / "sessions", ttl_hours=2)
    session = manager.create(sample_pdf, "sample.pdf", page_count=3)
    original_ids = list(session.page_ids)
    now[0] += 2 * 3600 + 1
    manager.get(session.id)
    assert manager.cleanup_expired() == 0
    assert session.page_ids == original_ids
    now[0] += 2 * 3600 + 1
    assert manager.cleanup_expired() == 1
    assert not session.path.parent.exists()


def test_session_orphan_cleanup_only_removes_expired_directories(tmp_path):
    manager = SessionManager(tmp_path / "sessions", ttl_hours=2)
    active_orphan = manager.base_dir / "recent"
    stale_orphan = manager.base_dir / "stale"
    active_orphan.mkdir()
    stale_orphan.mkdir()
    old = time.time() - 3 * 3600
    os.utime(stale_orphan, (old, old))
    assert manager.cleanup_orphans() == 1
    assert active_orphan.exists()
    assert not stale_orphan.exists()


def test_download_filename_is_safe():
    header = content_disposition('report\r\nX-Evil: yes".pdf')
    assert "\r" not in header and "\n" not in header
    assert 'filename="reportX-Evil: yes\'.pdf"' in header
    unicode_header = content_disposition("报告.pdf")
    assert "filename*=utf-8''" in unicode_header


def test_local_host_and_origin_are_enforced(client):
    allowed = client.get(
        "/api/health", headers={"Origin": "http://127.0.0.1:5173"}
    )
    assert allowed.status_code == 200
    assert allowed.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"

    bad_origin = client.get(
        "/api/health", headers={"Origin": "http://example.invalid"}
    )
    assert bad_origin.status_code == 403
    assert bad_origin.json()["code"] == "ORIGIN_NOT_ALLOWED"

    bad_host = client.get("/api/health", headers={"Host": "192.168.1.10:8000"})
    assert bad_host.status_code == 403
    assert bad_host.json()["code"] == "LOCAL_ACCESS_ONLY"
