"""LibreOffice headless wrapper for Office -> PDF conversion.

Mirrors Stirling-PDF's LibreOffice fallback chain (we go straight to
soffice since in-process conversion is out of scope for v0):
- isolated user profile per conversion so we never collide with an
  interactive LibreOffice instance
- serialized by the single document-job worker
- a separate cached readiness probe never blocks liveness
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
import uuid
from urllib.parse import quote

from .. import config
from ..core.errors import PdfError
from ..core.tempfile import temp_manager
from .windows_job import WindowsJobObject

logger = logging.getLogger("pdf_editor.libreoffice")
_LO_LOCK = asyncio.Lock()

OFFICE_EXTENSIONS = {
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".odt", ".ods", ".odp", ".rtf", ".txt", ".csv",
}


def soffice_available() -> bool:
    from pathlib import Path

    return Path(config.SOFFICE_PATH).exists()


def _create_lo_workdir() -> "Path":  # noqa: F821
    """Create a short unique path; LibreOffice creates deeply nested profiles."""
    from pathlib import Path

    path = config.TEMP_DIR / f"lo-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=False)
    temp_manager.register(path)
    return path


_probe_cache: bool | None = None
_probe_checked_at = 0.0
_probe_checked_wall: float | None = None
_probe_task: asyncio.Task | None = None


def _profile_url(profile: Path) -> str:
    # file:///E:/path — the drive colon must stay literal (encoding it as
    # %3A makes LibreOffice fail to resolve the profile: rc=1, no output).
    return f"file:///{quote(profile.as_posix(), safe=':/')}"


async def _probe_functional() -> bool | None:
    """Perform one real conversion; None means the bounded probe timed out."""
    if not soffice_available():
        return False
    config.ensure_dirs()
    from docx import Document

    from pathlib import Path

    root = _create_lo_workdir()
    try:
        src = root / "probe.docx"
        doc = Document()
        doc.add_paragraph("PDF Editor readiness probe")
        doc.save(str(src))
        outdir = root / "out"
        profile = root / "profile"
        outdir.mkdir()
        profile.mkdir()
        proc = await asyncio.create_subprocess_exec(
            config.SOFFICE_PATH,
            "--headless", "--norestore", "--nolockcheck", "--nodefault",
            f"-env:UserInstallation={_profile_url(profile)}",
            "--convert-to", "pdf", "--outdir", str(outdir), str(src),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        process_tree = WindowsJobObject(config.JOB_MEMORY_LIMIT_BYTES)
        try:
            process_tree.attach(proc.pid)
            await asyncio.wait_for(proc.communicate(), config.LO_READY_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            process_tree.close()
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            # A timeout only says this probe did not finish in its budget. It
            # does not prove LibreOffice is unusable; let readiness retry.
            return None
        except Exception:
            process_tree.close()
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            return False
        finally:
            process_tree.close()
        result = outdir / "probe.pdf"
        return proc.returncode == 0 and result.is_file() and result.stat().st_size > 0
    finally:
        # LibreOffice can release profile registry files a moment after its
        # process exits. Retry briefly, then leave the registered path for the
        # regular cleanup loop instead of failing the health endpoint.
        for attempt in range(21):
            if temp_manager._delete(root):
                temp_manager.unregister(root)
                break
            if attempt < 20:
                await asyncio.sleep(0.1)
            else:
                logger.warning("LibreOffice probe profile remains registered for retry: %s", root)


async def _refresh_readiness() -> None:
    global _probe_cache, _probe_checked_at, _probe_checked_wall, _probe_task
    try:
        _probe_cache = await _probe_functional()
    except Exception:
        logger.exception("LibreOffice readiness probe failed unexpectedly")
        _probe_cache = None
    finally:
        _probe_checked_at = time.monotonic()
        _probe_checked_wall = time.time()
        _probe_task = None


async def readiness_snapshot() -> dict:
    """Return cached LibreOffice readiness and trigger a non-blocking refresh."""
    global _probe_cache, _probe_checked_at, _probe_checked_wall, _probe_task
    if not soffice_available():
        _probe_cache = False
        _probe_checked_at = time.monotonic()
        _probe_checked_wall = time.time()
    elif _probe_task is None:
        retry_after = (
            config.LO_READY_RETRY_SECONDS
            if _probe_cache is None
            else config.LO_READY_CACHE_SECONDS
        )
        if time.monotonic() - _probe_checked_at >= retry_after:
            _probe_task = asyncio.create_task(_refresh_readiness(), name="libreoffice-readiness")
    status = "unknown" if _probe_cache is None else ("ready" if _probe_cache else "not_ready")
    return {
        "status": status,
        "libreofficeAvailable": soffice_available(),
        "checking": _probe_task is not None,
        "checkedAt": _probe_checked_wall,
    }


def soffice_functional() -> bool:
    """Synchronous compatibility probe for test collection and diagnostics."""
    global _probe_cache, _probe_checked_at, _probe_checked_wall
    if _probe_cache is not None and time.monotonic() - _probe_checked_at < config.LO_READY_CACHE_SECONDS:
        return _probe_cache
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _probe_cache = asyncio.run(_probe_functional())
        _probe_checked_at = time.monotonic()
        _probe_checked_wall = time.time()
        return _probe_cache is True
    # A running event loop must never be blocked by a health or request path.
    return bool(_probe_cache)


async def convert_to_pdf(src, timeout: int | None = None) -> "Path":  # noqa: F821
    """Convert an Office document to PDF. Returns a registered temp PDF."""
    from pathlib import Path

    src = Path(src)
    if not soffice_available():
        raise PdfError(503, "LibreOffice 未安装或未找到，无法进行 Office 转换")
    timeout = timeout or config.LO_TIMEOUT_SECONDS

    outdir = _create_lo_workdir()
    async with _LO_LOCK:
        cmd = [
            config.SOFFICE_PATH,
            "--headless",
            "--norestore",
            "--nolockcheck",
            "--nodefault",
            f"-env:UserInstallation={_profile_url(outdir / 'profile')}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(outdir),
            str(src),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            process_tree = WindowsJobObject(config.JOB_MEMORY_LIMIT_BYTES)
            try:
                try:
                    process_tree.attach(proc.pid)
                except Exception as exc:
                    proc.kill()
                    await proc.wait()
                    raise PdfError(
                        503,
                        "无法安全管理 LibreOffice 子进程树",
                        code="LO_PROCESS_TREE_UNAVAILABLE",
                    ) from exc
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout)
            except asyncio.TimeoutError:
                process_tree.close()
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
                raise PdfError(504, f"LibreOffice 转换超时（{timeout}s）")
            finally:
                process_tree.close()
        except PdfError:
            temp_manager.discard(outdir)
            raise
        except Exception as exc:
            temp_manager.discard(outdir)
            raise PdfError(500, f"LibreOffice 启动失败: {exc}") from exc

        out = outdir / (src.stem + ".pdf")
        if not out.exists() or out.stat().st_size == 0:
            detail = stderr.decode(errors="replace")[:500] if stderr else "无输出"
            temp_manager.discard(outdir)
            raise PdfError(500, f"LibreOffice 转换失败: {detail}")

        dest = None
        try:
            dest = temp_manager.create_temp_file(".pdf")
            shutil.copy2(out, dest)
            return dest
        except OSError as exc:
            if dest is not None:
                temp_manager.discard(dest)
            raise PdfError(507, "无法写入 LibreOffice 转换结果", code="DISK_WRITE_FAILED") from exc
        finally:
            temp_manager.discard(outdir)
