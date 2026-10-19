"""PDF Editor backend — FastAPI application entry.

Mirrors Stirling-PDF's SPDFApplication: single Spring Boot app with
grouped API routers; here FastAPI with APIRouters per group.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config
from .core.errors import PdfError, pdf_error_handler, unhandled_error_handler
from .core.tempfile import temp_manager
from .routers import convert, edit, general, misc
from .services import libreoffice
from .services.sessions import session_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("pdf_editor")


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.ensure_dirs()

    async def _cleanup_loop() -> None:
        while True:
            await asyncio.sleep(600)  # every 10 minutes
            removed = temp_manager.cleanup_old()
            sessions = session_manager.cleanup_expired()
            if removed or sessions:
                logger.info(
                    "Cleaned up %d expired temp entries, %d edit sessions",
                    removed,
                    sessions,
                )

    async def _lo_warmup() -> None:
        ok = await libreoffice.warmup()
        logger.info("LibreOffice warmup: %s", "ok" if ok else "unavailable")

    cleanup_task = asyncio.create_task(_cleanup_loop())
    warmup_task = asyncio.create_task(_lo_warmup())
    logger.info("PDF Editor backend started; temp dir: %s", config.TEMP_DIR)
    try:
        yield
    finally:
        cleanup_task.cancel()
        warmup_task.cancel()
        temp_manager.cleanup_old(0)  # dev shutdown: clear registry


app = FastAPI(title="PDF Editor", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(PdfError, pdf_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)

app.include_router(general.router)
app.include_router(convert.router)
app.include_router(misc.router)
app.include_router(edit.router)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "libreoffice": libreoffice.soffice_available()}
