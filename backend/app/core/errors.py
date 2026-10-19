"""Unified error type and FastAPI handlers."""
from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("pdf_editor")


class PdfError(Exception):
    """Domain error carrying an HTTP status code."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(message)


async def pdf_error_handler(_: Request, exc: PdfError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.message})


async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"error": "服务器内部错误", "detail": str(exc)},
    )
