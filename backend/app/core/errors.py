"""Unified error type and FastAPI handlers."""
from __future__ import annotations

import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger("pdf_editor")


class PdfError(Exception):
    """Domain error carrying a stable HTTP code and user-facing message."""

    def __init__(self, status_code: int, message: str, code: str | None = None):
        self.status_code = status_code
        self.message = message
        self.code = code or {
            400: "INVALID_INPUT",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            409: "CONFLICT",
            413: "UPLOAD_TOO_LARGE",
            422: "INVALID_INPUT",
            503: "SERVICE_UNAVAILABLE",
        }.get(status_code, "REQUEST_FAILED")
        super().__init__(message)


async def pdf_error_handler(_: Request, exc: PdfError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.message, "code": exc.code},
    )


async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"error": "服务器内部错误", "code": "INTERNAL_ERROR"},
    )


async def validation_error_handler(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    # Do not echo submitted text, filenames, or other values from `exc.errors()`.
    return JSONResponse(
        status_code=422,
        content={"error": "请求参数无效", "code": "INVALID_INPUT"},
    )
