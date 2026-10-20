"""Bounded in-process scheduler for isolated document jobs."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import multiprocessing
import os
import queue
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import config
from ..core.errors import PdfError
from ..core.tempfile import temp_manager
from .sessions import session_manager
from .windows_job import WindowsJobObject

logger = logging.getLogger("pdf_editor.jobs")

_TERMINAL = {"succeeded", "failed", "cancelled"}
_SESSION_MUTATIONS = {
    "editor_apply",
    "editor_add_text",
    "editor_add_image",
    "editor_rotate",
    "editor_delete",
    "editor_move",
}


@dataclass
class JobRecord:
    id: str
    operation: str
    payload: dict[str, Any]
    work_dir: Path
    idempotency_key: str
    request_hash: str
    input_paths: list[Path] = field(default_factory=list)
    session_id: str | None = None
    expected_revision: int | None = None
    cleanup_session_on_failure: bool = False
    state: str = "queued"
    progress: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    result: dict[str, Any] | None = None
    artifact_path: Path | None = None
    artifact_filename: str | None = None
    artifact_media_type: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    cancel_requested: bool = False
    process: multiprocessing.Process | None = None
    cancel_event: Any = None
    done_event: asyncio.Event | None = None


class JobManager:
    def __init__(self):
        self._queue: asyncio.Queue[str] | None = None
        self._loop_task: asyncio.Task | None = None
        self._records: dict[str, JobRecord] = {}
        self._idempotency: dict[tuple[str, str], tuple[str, str, float]] = {}
        self._active_id: str | None = None
        self._ctx = None
        self._stopping = False

    async def start(self) -> None:
        if self._loop_task is not None:
            return
        self._ctx = multiprocessing.get_context("spawn")
        self._queue = asyncio.Queue(maxsize=config.JOB_QUEUE_CAPACITY)
        self._stopping = False
        self._loop_task = asyncio.create_task(self._run_loop(), name="pdf-editor-job-loop")

    async def stop(self) -> None:
        self._stopping = True
        if self._active_id is not None:
            active = self._records.get(self._active_id)
            if active is not None:
                active.cancel_requested = True
                if active.cancel_event is not None:
                    active.cancel_event.set()
                process = active.process
                if process is not None and process.is_alive():
                    process.terminate()
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None
        if self._queue is not None:
            while not self._queue.empty():
                try:
                    job_id = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                job = self._records.get(job_id)
                if job is not None and job.state == "queued":
                    self._finish(job, "cancelled", {"code": "SERVER_SHUTDOWN", "message": "服务正在关闭"})
                self._queue.task_done()
            self._queue = None

    @staticmethod
    def fingerprint(operation: str, arguments: dict[str, Any], input_hashes: list[str]) -> str:
        stable_arguments = {
            key: value
            for key, value in arguments.items()
            if not key.endswith("_path") and key not in {"source_paths", "image_paths"}
        }
        body = json.dumps(
            {"operation": operation, "arguments": stable_arguments, "inputs": input_hashes},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(body).hexdigest()

    async def submit(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
        request_hash: str,
        input_paths: list[Path] | None = None,
        session_id: str | None = None,
        expected_revision: int | None = None,
        cleanup_session_on_failure: bool = False,
        idempotency_scope: str | None = None,
    ) -> JobRecord:
        if self._queue is None:
            raise PdfError(503, "后台任务服务尚未启动", code="JOB_SERVICE_UNAVAILABLE")
        if not idempotency_key or len(idempotency_key) > 128:
            raise PdfError(422, "必须提供有效的 Idempotency-Key", code="IDEMPOTENCY_KEY_REQUIRED")

        paths = list(input_paths or [])
        namespace = idempotency_scope or session_id or "local"
        idem_index = (namespace, idempotency_key)
        previous = self._idempotency.get(idem_index)
        if previous is not None:
            previous_hash, previous_job_id, _ = previous
            if previous_hash != request_hash:
                self._discard_inputs(paths)
                if cleanup_session_on_failure and session_id:
                    session_manager.close(session_id)
                raise PdfError(409, "Idempotency-Key 已用于不同请求", code="IDEMPOTENCY_KEY_REUSED")
            self._discard_inputs(paths)
            if cleanup_session_on_failure and session_id:
                session_manager.close(session_id)
            existing = self._records.get(previous_job_id)
            if existing is not None:
                return existing

        if self._queue.full():
            self._discard_inputs(paths)
            if cleanup_session_on_failure and session_id:
                session_manager.close(session_id)
            raise PdfError(429, "任务队列已满，请稍后重试", code="JOB_QUEUE_FULL")

        job_id = uuid.uuid4().hex
        work_dir = config.TEMP_DIR / "jobs" / job_id
        work_dir.mkdir(parents=True, exist_ok=False)
        job = JobRecord(
            id=job_id,
            operation=operation,
            payload=dict(payload),
            work_dir=work_dir,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            input_paths=paths,
            session_id=session_id,
            expected_revision=expected_revision,
            cleanup_session_on_failure=cleanup_session_on_failure,
            done_event=asyncio.Event(),
        )

        session = None
        if session_id is not None and expected_revision is not None:
            try:
                session = session_manager.get(session_id)
                with session.lock:
                    if session.revision != expected_revision:
                        raise PdfError(409, "文档版本已变化，请刷新后重试", code="REVISION_CONFLICT")
                    if session.pending_job_id is not None:
                        raise PdfError(409, "该文档已有任务正在处理", code="SESSION_BUSY")
                    session.pending_job_id = job_id
            except BaseException:
                shutil.rmtree(work_dir, ignore_errors=True)
                self._discard_inputs(paths)
                if cleanup_session_on_failure and session_id:
                    session_manager.close(session_id)
                raise

        try:
            self._queue.put_nowait(job_id)
        except asyncio.QueueFull:
            if session is not None:
                with session.lock:
                    if session.pending_job_id == job_id:
                        session.pending_job_id = None
            shutil.rmtree(work_dir, ignore_errors=True)
            self._discard_inputs(paths)
            if cleanup_session_on_failure and session_id:
                session_manager.close(session_id)
            raise PdfError(429, "任务队列已满，请稍后重试", code="JOB_QUEUE_FULL")

        self._records[job_id] = job
        self._idempotency[idem_index] = (request_hash, job_id, time.time())
        return job

    def get(self, job_id: str) -> JobRecord:
        job = self._records.get(job_id)
        if job is None:
            raise PdfError(404, "任务不存在或已过期", code="JOB_NOT_FOUND")
        return job

    def serialize(self, job: JobRecord) -> dict[str, Any]:
        result: dict[str, Any] = {
            "jobId": job.id,
            "state": job.state,
            "progress": job.progress,
            "error": job.error,
        }
        if job.state == "succeeded":
            if job.artifact_path is not None:
                result["resultUrl"] = f"/api/v1/jobs/{job.id}/result"
                result["filename"] = job.artifact_filename
            if job.result is not None:
                result["result"] = job.result
        return result

    def artifact(self, job_id: str) -> tuple[Path, str, str]:
        job = self.get(job_id)
        if job.state != "succeeded" or job.artifact_path is None:
            raise PdfError(409, "任务结果尚未就绪", code="JOB_RESULT_NOT_READY")
        if not job.artifact_path.is_file():
            raise PdfError(404, "任务结果已过期", code="JOB_RESULT_EXPIRED")
        return (
            job.artifact_path,
            job.artifact_filename or job.artifact_path.name,
            job.artifact_media_type or "application/octet-stream",
        )

    async def cancel(self, job_id: str) -> JobRecord:
        job = self.get(job_id)
        if job.state == "queued":
            job.cancel_requested = True
            self._finish(job, "cancelled", {"code": "CANCELLED", "message": "任务已取消"})
            self._release_session(job)
            self._discard_inputs(job.input_paths)
            job.input_paths.clear()
            return job
        if job.state in {"committing", "succeeded"}:
            raise PdfError(409, "任务已进入提交阶段，无法取消", code="JOB_COMMITTING")
        if job.state in {"failed", "cancelled"}:
            raise PdfError(409, "任务已结束，无法取消", code="JOB_NOT_CANCELLABLE")
        job.cancel_requested = True
        if job.cancel_event is not None:
            job.cancel_event.set()
        if job.process is not None and job.process.is_alive():
            job.process.terminate()
        if job.done_event is not None:
            try:
                await asyncio.wait_for(job.done_event.wait(), timeout=5)
            except asyncio.TimeoutError:
                process = job.process
                if process is not None and process.is_alive():
                    process.kill()
                await asyncio.wait_for(job.done_event.wait(), timeout=5)
        return job

    def cleanup_expired(self) -> int:
        cutoff = time.time() - config.JOB_RESULT_TTL_HOURS * 3600
        expired = [
            job_id
            for job_id, job in self._records.items()
            if job.state in _TERMINAL
            and job.finished_at is not None
            and job.finished_at < cutoff
        ]
        for job_id in expired:
            job = self._records.pop(job_id)
            shutil.rmtree(job.work_dir, ignore_errors=True)
            for key, entry in list(self._idempotency.items()):
                if entry[1] == job_id:
                    self._idempotency.pop(key, None)
        return len(expired)

    async def _run_loop(self) -> None:
        assert self._queue is not None
        while True:
            job_id = await self._queue.get()
            job = self._records.get(job_id)
            if job is None or job.state != "queued":
                self._queue.task_done()
                continue
            self._active_id = job_id
            try:
                await self._execute(job)
            except asyncio.CancelledError:
                if job.state not in _TERMINAL:
                    self._finish(job, "cancelled", {"code": "SERVER_SHUTDOWN", "message": "服务正在关闭"})
                raise
            except Exception:
                logger.exception("Job %s failed in scheduler", job_id)
                self._finish(job, "failed", {"code": "WORKER_FAILED", "message": "PDF 处理任务失败"})
                self._release_session(job)
                self._discard_inputs(job.input_paths)
                job.input_paths.clear()
            finally:
                self._active_id = None
                self._queue.task_done()

    async def _execute(self, job: JobRecord) -> None:
        if job.cancel_requested:
            self._finish(job, "cancelled", {"code": "CANCELLED", "message": "任务已取消"})
            self._release_session(job)
            return
        if job.session_id and job.expected_revision is not None:
            session = session_manager.get(job.session_id)
            with session.lock:
                if session.revision != job.expected_revision or session.pending_job_id != job.id:
                    self._finish(job, "failed", {"code": "REVISION_CONFLICT", "message": "文档版本已变化"})
                    self._release_session(job)
                    self._discard_inputs(job.input_paths)
                    job.input_paths.clear()
                    return

        job.state = "running"
        job.progress = {"phase": "starting", "completed": 0, "total": None, "unit": None}
        context = self._ctx or multiprocessing.get_context("spawn")
        progress_queue = context.Queue()
        cancel_event = context.Event()
        job.cancel_event = cancel_event
        result_file = job.work_dir / "worker-result.json"
        from .pdf_worker import worker_entry

        process = context.Process(
            target=worker_entry,
            args=(
                job.operation,
                job.payload,
                str(job.work_dir),
                str(result_file),
                progress_queue,
                cancel_event,
                config.JOB_MEMORY_LIMIT_BYTES,
            ),
            name=f"pdf-job-{job.id[:8]}",
        )
        job.process = process
        guard = WindowsJobObject(config.JOB_MEMORY_LIMIT_BYTES)
        started = time.monotonic()
        try:
            process.start()
            try:
                guard.attach(process.pid)
            except Exception as exc:
                process.terminate()
                await self._wait_process(process, 2)
                self._finish(
                    job,
                    "failed",
                    {"code": "MEMORY_LIMIT_UNAVAILABLE", "message": "无法设置任务内存上限"},
                )
                logger.warning("Could not attach worker %s to memory Job Object: %s", job.id, exc)
                self._release_session(job)
                self._discard_inputs(job.input_paths)
                job.input_paths.clear()
                return

            while process.is_alive():
                self._drain_progress(job, progress_queue)
                if job.cancel_requested:
                    process.terminate()
                    await self._wait_process(process, 2)
                    self._finish(job, "cancelled", {"code": "CANCELLED", "message": "任务已取消"})
                    self._release_session(job)
                    self._discard_inputs(job.input_paths)
                    job.input_paths.clear()
                    return
                if time.monotonic() - started > config.JOB_TIMEOUT_SECONDS:
                    process.terminate()
                    await self._wait_process(process, 2)
                    self._finish(job, "failed", {"code": "JOB_TIMEOUT", "message": "任务超过 15 分钟上限"})
                    self._release_session(job)
                    self._discard_inputs(job.input_paths)
                    job.input_paths.clear()
                    return
                await asyncio.sleep(0.05)

            process.join()
            self._drain_progress(job, progress_queue)
            if job.cancel_requested:
                self._finish(job, "cancelled", {"code": "CANCELLED", "message": "任务已取消"})
                self._release_session(job)
                return
            if process.exitcode == 70 or guard.memory_limited:
                self._finish(
                    job,
                    "failed",
                    {"code": "MEMORY_BUDGET_EXCEEDED", "message": "任务超过 2 GiB 内存预算"},
                )
                self._release_session(job)
                self._discard_inputs(job.input_paths)
                job.input_paths.clear()
                return
            if not result_file.is_file():
                self._finish(
                    job,
                    "failed",
                    {"code": "WORKER_FAILED", "message": "PDF 处理任务未能完成"},
                )
                self._release_session(job)
                self._discard_inputs(job.input_paths)
                job.input_paths.clear()
                return
            envelope = json.loads(result_file.read_text(encoding="utf-8"))
            if not envelope.get("ok"):
                self._finish(job, "failed", envelope.get("error") or {
                    "code": "WORKER_FAILED", "message": "PDF 处理任务失败"
                })
                self._release_session(job)
                self._discard_inputs(job.input_paths)
                job.input_paths.clear()
                if job.cleanup_session_on_failure and job.session_id:
                    session_manager.close(job.session_id)
                return
            await self._accept_result(job, envelope["result"])
        finally:
            guard.close()
            if process.pid is not None and process.is_alive():
                process.kill()
                await self._wait_process(process, 2)
            try:
                progress_queue.close()
                progress_queue.join_thread()
            except Exception:
                pass
            job.process = None
            job.cancel_event = None
            self._discard_inputs(job.input_paths)
            job.input_paths.clear()

    async def _accept_result(self, job: JobRecord, result: dict[str, Any]) -> None:
        kind = result.get("kind")
        if kind == "session_mutation":
            artifact = job.work_dir / result["artifact"]
            session = session_manager.get(job.session_id or "")
            with session.lock:
                if job.cancel_requested:
                    self._finish(job, "cancelled", {"code": "CANCELLED", "message": "任务已取消"})
                    self._release_session(job)
                    return
                if (
                    session.closed
                    or session.revision != job.expected_revision
                    or session.pending_job_id != job.id
                ):
                    self._finish(job, "failed", {"code": "REVISION_CONFLICT", "message": "文档版本已变化"})
                    self._release_session(job)
                    return
                job.state = "committing"
                try:
                    os.replace(artifact, session.path)
                except OSError:
                    self._finish(job, "failed", {"code": "COMMIT_FAILED", "message": "无法安全提交 PDF 修改"})
                    self._release_session(job)
                    return
                session.page_ids = list(result["data"]["pageIds"])
                session.revision += 1
                result["data"]["revision"] = session.revision
                job.result = result["data"]
                session.pending_job_id = None
            self._finish(job, "succeeded")
            return

        if kind == "file":
            job.artifact_path = job.work_dir / result["artifact"]
            job.artifact_filename = result["filename"]
            job.artifact_media_type = result.get("mediaType", "application/octet-stream")
            self._finish(job, "succeeded")
            return

        if kind == "json":
            job.result = result.get("data")
            self._finish(job, "succeeded")
            return

        self._finish(job, "failed", {"code": "WORKER_FAILED", "message": "任务结果格式无效"})
        self._release_session(job)

    def _drain_progress(self, job: JobRecord, progress_queue) -> None:
        while True:
            try:
                job.progress = progress_queue.get_nowait()
            except queue.Empty:
                return
            except Exception:
                return

    @staticmethod
    async def _wait_process(process: multiprocessing.Process, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while process.is_alive() and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        if process.is_alive():
            process.kill()
            while process.is_alive():
                await asyncio.sleep(0.05)
        process.join()

    def _release_session(self, job: JobRecord) -> None:
        if not job.session_id:
            return
        try:
            session = session_manager._sessions.get(job.session_id)
            if session is not None:
                with session.lock:
                    if session.pending_job_id == job.id:
                        session.pending_job_id = None
        except Exception:
            logger.exception("Could not release session lock for job %s", job.id)

    def _finish(self, job: JobRecord, state: str, error: dict[str, str] | None = None) -> None:
        if job.state in _TERMINAL:
            return
        job.state = state
        job.error = error
        job.finished_at = time.time()
        if state in {"failed", "cancelled"}:
            shutil.rmtree(job.work_dir, ignore_errors=True)
            if job.cleanup_session_on_failure and job.session_id:
                session_manager.close(job.session_id)
                job.cleanup_session_on_failure = False
        if job.done_event is not None:
            job.done_event.set()

    @staticmethod
    def _discard_inputs(paths: list[Path]) -> None:
        for path in paths:
            temp_manager.discard(path)


job_manager = JobManager()
