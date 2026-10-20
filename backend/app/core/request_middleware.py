"""Small ASGI guards for local-only access and streamed upload limits."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

ASGIApp = Callable[[dict[str, Any], Callable[..., Awaitable[Any]], Callable[..., Awaitable[None]]], Awaitable[None]]


async def _json_error(send, status: int, message: str, code: str) -> None:
    body = json.dumps({"error": message, "code": code}, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class LocalAccessMiddleware:
    """Reject non-loopback Host headers and browser requests from other origins."""

    def __init__(self, app: ASGIApp, ui_origin: str):
        self.app = app
        self.ui_origin = ui_origin

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        host = headers.get(b"host", b"").decode("latin-1").lower()
        host_name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        if host_name != "127.0.0.1":
            await _json_error(send, 403, "仅允许本机访问", "LOCAL_ACCESS_ONLY")
            return

        origin = headers.get(b"origin")
        if origin is not None:
            supplied_origin = origin.decode("latin-1").rstrip("/")
            if supplied_origin != self.ui_origin.rstrip("/"):
                await _json_error(send, 403, "请求来源不受允许", "ORIGIN_NOT_ALLOWED")
                return

        await self.app(scope, receive, send)


class RequestBodyLimitMiddleware:
    """Count multipart request bytes as they arrive without buffering the body."""

    def __init__(self, app: ASGIApp, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes
        self.limit_label = f"{max_bytes / (1024 * 1024):g} MiB"

    async def _too_large(self, send) -> None:
        await _json_error(
            send,
            413,
            f"单次上传请求超过 {self.limit_label} 限额",
            "UPLOAD_TOO_LARGE",
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_type = headers.get(b"content-type", b"").lower()
        if not content_type.startswith(b"multipart/form-data"):
            await self.app(scope, receive, send)
            return

        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_bytes:
                    await self._too_large(send)
                    return
            except ValueError:
                # Content-Length is advisory; streamed bytes below remain authoritative.
                pass

        total = 0
        rejected = False
        response_started = False

        async def limited_receive():
            nonlocal total, rejected, response_started
            message = await receive()
            if message.get("type") == "http.request" and not rejected:
                total += len(message.get("body", b""))
                if total > self.max_bytes:
                    rejected = True
                    response_started = True
                    await self._too_large(send)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message):
            nonlocal response_started
            if rejected:
                return
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except BaseException:
            if not rejected:
                raise
        finally:
            # Starlette's multipart parser closes spooled UploadFiles while unwinding
            # a disconnect. Suppressing a second response preserves the 413 contract.
            if rejected and not response_started:
                await self._too_large(send)
