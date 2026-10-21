from __future__ import annotations

import asyncio
import os

import pytest

from app.core.errors import PdfError
from app.services.jobs import JobManager, JobRecord
from app.services.sessions import session_manager


async def _wait_terminal(job, timeout: float = 10.0):
    await asyncio.wait_for(job.done_event.wait(), timeout=timeout)
    return job


def test_spawn_worker_completes_and_idempotency_is_enforced(tmp_path):
    async def scenario():
        manager = JobManager()
        await manager.start()
        try:
            job = await manager.submit(
                "test_sleep",
                {"seconds": 0.05},
                idempotency_key="same-key",
                request_hash="hash-a",
            )
            duplicate = await manager.submit(
                "test_sleep",
                {"seconds": 0.05},
                idempotency_key="same-key",
                request_hash="hash-a",
            )
            assert duplicate.id == job.id
            with pytest.raises(PdfError) as mismatch:
                await manager.submit(
                    "test_sleep",
                    {"seconds": 0.05},
                    idempotency_key="same-key",
                    request_hash="hash-b",
                )
            assert mismatch.value.code == "IDEMPOTENCY_KEY_REUSED"

            await _wait_terminal(job)
            assert job.state == "succeeded"
            assert job.result == {"slept": 0.05}
        finally:
            await manager.stop()

    asyncio.run(scenario())


def test_internal_worker_cancellation_terminates_process():
    async def scenario():
        manager = JobManager()
        await manager.start()
        task = asyncio.create_task(
            manager.run_internal("test_sleep", {"seconds": 10})
        )
        try:
            deadline = asyncio.get_running_loop().time() + 5
            while manager._active_id is None and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.01)
            assert manager._active_id is not None
            job_id = manager._active_id
            job = manager.get(job_id)
            process = job.process
            assert process is not None and process.is_alive()

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert process.exitcode is not None
            assert job.state == "cancelled"
            assert job_id not in manager._records
        finally:
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            await manager.stop()

    asyncio.run(scenario())


@pytest.mark.parametrize("state", ["queued", "running", "succeeded", "failed", "cancelled"])
def test_idempotent_retry_resolves_existing_job_before_preflight(state, tmp_path):
    manager = JobManager()
    job = JobRecord(
        id="existing-job",
        operation="editor_apply",
        payload={},
        work_dir=tmp_path / "existing-job",
        idempotency_key="retry-key",
        request_hash="same-request",
        session_id="session-id",
        state=state,
    )
    manager._records[job.id] = job
    manager._idempotency[("session-id", "retry-key")] = ("same-request", job.id, 0.0)
    preflight_calls = []

    async def scenario():
        manager._queue = asyncio.Queue()
        duplicate = await manager.submit(
            "editor_apply",
            {},
            idempotency_key="retry-key",
            request_hash="same-request",
            session_id="session-id",
            expected_revision=0,
            preflight=lambda: preflight_calls.append(True),
        )
        assert duplicate is job
        assert duplicate.state == state

        with pytest.raises(PdfError) as mismatch:
            await manager.submit(
                "editor_apply",
                {},
                idempotency_key="retry-key",
                request_hash="different-request",
                session_id="session-id",
                expected_revision=0,
                preflight=lambda: preflight_calls.append(True),
            )
        assert mismatch.value.code == "IDEMPOTENCY_KEY_REUSED"
        assert preflight_calls == []

    asyncio.run(scenario())


def test_bounded_queue_and_running_job_cancellation():
    async def scenario():
        manager = JobManager()
        await manager.start()
        try:
            running = await manager.submit(
                "test_sleep", {"seconds": 5},
                idempotency_key="running", request_hash="running",
            )
            deadline = asyncio.get_running_loop().time() + 5
            while running.state != "running" and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.02)
            assert running.state == "running"

            queued = await manager.submit(
                "test_sleep", {"seconds": 0.1},
                idempotency_key="queued", request_hash="queued",
            )
            with pytest.raises(PdfError) as full:
                await manager.submit(
                    "test_sleep", {"seconds": 0.1},
                    idempotency_key="overflow", request_hash="overflow",
                )
            assert full.value.status_code == 429
            assert full.value.code == "JOB_QUEUE_FULL"

            assert (await manager.cancel(queued.id)).state == "cancelled"
            assert (await manager.cancel(running.id)).state == "cancelled"
            assert not running.work_dir.exists()
        finally:
            await manager.stop()

    asyncio.run(scenario())


def test_worker_timeout_terminates_without_result(tmp_path, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "JOB_TIMEOUT_SECONDS", 0.1)

    async def scenario():
        manager = JobManager()
        await manager.start()
        try:
            job = await manager.submit(
                "test_sleep", {"seconds": 5},
                idempotency_key="timeout", request_hash="timeout",
            )
            await _wait_terminal(job, timeout=8)
            assert job.state == "failed"
            assert job.error["code"] == "JOB_TIMEOUT"
            assert not job.work_dir.exists()
        finally:
            await manager.stop()

    asyncio.run(scenario())


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object memory cap")
def test_memory_limit_terminates_worker_and_cleans_artifacts(monkeypatch):
    from app import config

    monkeypatch.setattr(config, "JOB_MEMORY_LIMIT_BYTES", 256 * 1024 * 1024)

    async def scenario():
        manager = JobManager()
        await manager.start()
        try:
            job = await manager.submit(
                "test_allocate",
                {"bytes": 512 * 1024 * 1024},
                idempotency_key="memory-limit",
                request_hash="memory-limit",
            )
            await _wait_terminal(job, timeout=15)
            assert job.state == "failed"
            assert job.error["code"] == "MEMORY_BUDGET_EXCEEDED"
            assert not job.work_dir.exists()
        finally:
            await manager.stop()

    asyncio.run(scenario())


def test_session_job_commit_increments_revision_and_is_atomic(sample_pdf, tmp_path):
    async def scenario():
        manager = JobManager()
        await manager.start()
        session = session_manager.create(sample_pdf, "sample.pdf", page_count=3)
        before = session.path.read_bytes()
        try:
            job = await manager.submit(
                "test_session_mutation",
                {
                    "source_path": str(session.path),
                    "page_ids": list(session.page_ids),
                },
                idempotency_key="session-write",
                request_hash="session-write",
                session_id=session.id,
                expected_revision=0,
            )
            await _wait_terminal(job)
            assert job.state == "succeeded"
            assert session.revision == 1
            assert session.pending_job_id is None
            assert session.path.read_bytes() == before
            with pytest.raises(PdfError) as too_late:
                await manager.cancel(job.id)
            assert too_late.value.code == "JOB_COMMITTING"
            with pytest.raises(PdfError) as stale:
                await manager.submit(
                    "test_session_mutation",
                    {
                        "source_path": str(session.path),
                        "page_ids": list(session.page_ids),
                    },
                    idempotency_key="stale-write",
                    request_hash="stale-write",
                    session_id=session.id,
                    expected_revision=0,
                )
            assert stale.value.code == "REVISION_CONFLICT"
        finally:
            session_manager.close(session.id)
            await manager.stop()

    asyncio.run(scenario())


def test_cancelled_session_mutation_never_replaces_source(sample_pdf):
    async def scenario():
        manager = JobManager()
        await manager.start()
        session = session_manager.create(sample_pdf, "sample.pdf", page_count=3)
        before = session.path.read_bytes()
        try:
            job = await manager.submit(
                "test_session_mutation",
                {
                    "source_path": str(session.path),
                    "page_ids": list(session.page_ids),
                    "seconds": 5,
                },
                idempotency_key="cancel-session-write",
                request_hash="cancel-session-write",
                session_id=session.id,
                expected_revision=0,
            )
            deadline = asyncio.get_running_loop().time() + 5
            while job.state != "running" and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.02)
            assert job.state == "running"
            await manager.cancel(job.id)
            assert job.state == "cancelled"
            assert session.revision == 0
            assert session.pending_job_id is None
            assert session.path.read_bytes() == before
        finally:
            session_manager.close(session.id)
            await manager.stop()

    asyncio.run(scenario())


def test_cancel_after_commit_point_is_rejected_and_commit_finishes(sample_pdf):
    async def scenario():
        manager = JobManager()
        await manager.start()
        session = session_manager.create(sample_pdf, "sample.pdf", page_count=3)
        original = manager._accept_result
        entered_commit = asyncio.Event()
        allow_commit = asyncio.Event()
        try:
            async def delayed_accept(job, result):
                job.state = "committing"
                entered_commit.set()
                await allow_commit.wait()
                await original(job, result)

            manager._accept_result = delayed_accept
            job = await manager.submit(
                "test_session_mutation",
                {"source_path": str(session.path), "page_ids": list(session.page_ids)},
                idempotency_key="cancel-after-commit-point",
                request_hash="cancel-after-commit-point",
                session_id=session.id,
                expected_revision=0,
            )
            await asyncio.wait_for(entered_commit.wait(), timeout=10)
            with pytest.raises(PdfError) as too_late:
                await manager.cancel(job.id)
            assert too_late.value.code == "JOB_COMMITTING"
            allow_commit.set()
            await _wait_terminal(job)
            assert job.state == "succeeded"
            assert session.revision == 1
            assert session.pending_job_id is None
        finally:
            allow_commit.set()
            manager._accept_result = original
            session_manager.close(session.id)
            await manager.stop()

    asyncio.run(scenario())


def test_history_capacity_requires_explicit_evict_or_no_undo_policy(sample_pdf, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "EDITOR_HISTORY_MAX_STEPS", 1)

    async def scenario():
        manager = JobManager()
        await manager.start()
        session = session_manager.create(sample_pdf, "sample.pdf", page_count=3)
        try:
            first = await manager.submit(
                "editor_rotate",
                {"source_path": str(session.path), "page_ids": list(session.page_ids), "page": 0, "angle": 90},
                idempotency_key="history-step-1",
                request_hash="history-step-1",
                session_id=session.id,
                expected_revision=0,
            )
            await _wait_terminal(first)
            assert first.state == "succeeded"
            original_entry = session.undo_stack[-1].id

            with pytest.raises(PdfError) as full:
                await manager.submit(
                    "editor_rotate",
                    {"source_path": str(session.path), "page_ids": list(session.page_ids), "page": 0, "angle": 90},
                    idempotency_key="history-step-2-blocked",
                    request_hash="history-step-2",
                    session_id=session.id,
                    expected_revision=1,
                )
            assert full.value.code == "HISTORY_CAPACITY_CONFIRMATION_REQUIRED"

            second = await manager.submit(
                "editor_rotate",
                {"source_path": str(session.path), "page_ids": list(session.page_ids), "page": 0, "angle": 90},
                idempotency_key="history-step-2-evict",
                request_hash="history-step-2",
                session_id=session.id,
                expected_revision=1,
                history_policy="evict_oldest",
            )
            await _wait_terminal(second)
            assert second.state == "succeeded"
            assert len(session.undo_stack) == 1
            assert session.undo_stack[0].id != original_entry

            third = await manager.submit(
                "editor_rotate",
                {"source_path": str(session.path), "page_ids": list(session.page_ids), "page": 0, "angle": 90},
                idempotency_key="history-step-3-no-undo",
                request_hash="history-step-3",
                session_id=session.id,
                expected_revision=2,
                history_policy="no_undo",
            )
            await _wait_terminal(third)
            assert third.state == "succeeded"
            assert session.undo_stack == []
            assert session.redo_stack == []
        finally:
            session_manager.close(session.id)
            await manager.stop()

    asyncio.run(scenario())
