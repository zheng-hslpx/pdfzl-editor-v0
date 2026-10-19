"""LibreOffice headless wrapper for Office -> PDF conversion.

Mirrors Stirling-PDF's LibreOffice fallback chain (we go straight to
soffice since in-process conversion is out of scope for v0):
- isolated user profile (-env:UserInstallation) so we never collide
  with a running LibreOffice instance
- serialized via an asyncio lock (shared profile is not concurrency-safe)
- startup warmup amortizes the 10-30s first-run profile initialization
"""
from __future__ import annotations

import asyncio
import shutil
from urllib.parse import quote

from .. import config
from ..core.errors import PdfError
from ..core.tempfile import temp_manager

_LO_LOCK = asyncio.Lock()
_LO_PROFILE = config.TEMP_DIR / "lo_profile"
_warmup_done = False

OFFICE_EXTENSIONS = {
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".odt", ".ods", ".odp", ".rtf", ".txt", ".csv",
}


def soffice_available() -> bool:
    from pathlib import Path

    return Path(config.SOFFICE_PATH).exists()


_probe_cache: bool | None = None


def soffice_functional() -> bool:
    """True if soffice can actually convert a document.

    A bare `--version` check is unreliable (this machine's build exits 0
    silently even when broken), so we probe with a real minimal docx -> pdf
    conversion. Result is cached per process; the first call may take
    10-30s while LibreOffice initializes its profile.
    """
    global _probe_cache
    if _probe_cache is not None:
        return _probe_cache
    from pathlib import Path

    if not Path(config.SOFFICE_PATH).exists():
        _probe_cache = False
        return False

    import subprocess
    import tempfile

    try:
        from docx import Document

        with tempfile.TemporaryDirectory(prefix="lo-probe-") as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "probe.docx"
            doc = Document()
            doc.add_paragraph("probe")
            doc.save(str(src))
            outdir = tmp_path / "out"
            outdir.mkdir()
            profile = tmp_path / "profile"
            profile.mkdir()
            result = subprocess.run(
                [
                    config.SOFFICE_PATH,
                    "--headless",
                    "--norestore",
                    "--nolockcheck",
                    "--nodefault",
                    f"-env:UserInstallation=file:///{quote(profile.as_posix(), safe=':/')}",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(outdir),
                    str(src),
                ],
                capture_output=True,
                text=True,
                timeout=180,
            )
            _probe_cache = result.returncode == 0 and (outdir / "probe.pdf").exists()
    except Exception:
        _probe_cache = False
    return _probe_cache


def _profile_url() -> str:
    # file:///E:/path — the drive colon must stay literal (encoding it as
    # %3A makes LibreOffice fail to resolve the profile: rc=1, no output).
    return f"file:///{quote(_LO_PROFILE.as_posix(), safe=':/')}"


async def convert_to_pdf(src, timeout: int | None = None) -> "Path":  # noqa: F821
    """Convert an Office document to PDF. Returns a registered temp PDF."""
    from pathlib import Path

    src = Path(src)
    if not soffice_available():
        raise PdfError(503, "LibreOffice 未安装或未找到，无法进行 Office 转换")
    timeout = timeout or config.LO_TIMEOUT_SECONDS

    outdir = temp_manager.create_temp_dir()
    async with _LO_LOCK:
        cmd = [
            config.SOFFICE_PATH,
            "--headless",
            "--norestore",
            "--nolockcheck",
            "--nodefault",
            f"-env:UserInstallation={_profile_url()}",
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
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise PdfError(504, f"LibreOffice 转换超时（{timeout}s）")
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

        dest = temp_manager.create_temp_file(".pdf")
        shutil.copy2(out, dest)
        temp_manager.discard(outdir)
        return dest


async def warmup() -> bool:
    """Convert a throwaway docx once to initialize the shared LO profile."""
    global _warmup_done
    if _warmup_done:
        return True
    if not soffice_available():
        return False
    from pathlib import Path

    try:
        from docx import Document

        src = temp_manager.create_temp_file(".docx")
        doc = Document()
        doc.add_paragraph("warmup")
        doc.save(str(src))
        await convert_to_pdf(src, timeout=120)
        temp_manager.discard(src)
        _warmup_done = True
        return True
    except Exception:
        return False
