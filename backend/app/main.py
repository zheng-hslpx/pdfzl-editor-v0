"""PDF Editor backend — FastAPI application entry.

Mirrors Stirling-PDF's SPDFApplication: single Spring Boot app with
grouped API routers; here FastAPI with APIRouters per group.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from . import config
from .core.errors import (
    PdfError,
    pdf_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from .core.request_middleware import LocalAccessMiddleware, RequestBodyLimitMiddleware
from .core.tempfile import temp_manager
from .routers import convert, edit, general, jobs, misc
from .services import libreoffice
from .services.sessions import session_manager
from .services.jobs import job_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("pdf_editor")


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.ensure_dirs()
    orphaned_temp = temp_manager.cleanup_orphans()
    orphaned_sessions = session_manager.cleanup_orphans()
    await job_manager.start()
    if orphaned_temp or orphaned_sessions:
        logger.info(
            "Removed %d orphan temporary entries and %d orphan edit sessions",
            orphaned_temp,
            orphaned_sessions,
        )

    async def _cleanup_loop() -> None:
        while True:
            await asyncio.sleep(600)  # every 10 minutes
            try:
                jobs = job_manager.cleanup_expired()
                removed = temp_manager.cleanup_old()
                sessions = session_manager.cleanup_expired()
                orphaned_sessions = session_manager.cleanup_orphans()
                if removed or sessions or orphaned_sessions or jobs:
                    logger.info(
                        "Cleaned up %d jobs, %d temp entries, %d sessions, %d orphan sessions",
                        jobs,
                        removed,
                        sessions,
                        orphaned_sessions,
                    )
            except Exception:
                logger.exception("Temporary cleanup pass failed; scheduler will continue")

    cleanup_task = asyncio.create_task(_cleanup_loop())
    logger.info("PDF Editor backend started; temp dir: %s", config.TEMP_DIR)
    try:
        yield
    finally:
        cleanup_task.cancel()
        await job_manager.stop()
        session_manager.close_all()
        temp_manager.cleanup_old(0)  # dev shutdown: clear registry


app = FastAPI(title="PDF Editor", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Idempotency-Key"],
)
app.add_middleware(LocalAccessMiddleware, ui_origin=config.UI_ORIGIN)
app.add_middleware(RequestBodyLimitMiddleware, max_bytes=config.MAX_UPLOAD_BYTES)

app.add_exception_handler(PdfError, pdf_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)

app.include_router(general.router)
app.include_router(convert.router)
app.include_router(misc.router)
app.include_router(edit.router)
app.include_router(jobs.router)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/health/ready")
async def readiness() -> dict:
    return await libreoffice.readiness_snapshot()
