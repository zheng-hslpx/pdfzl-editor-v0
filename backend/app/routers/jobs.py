"""Polling, cancellation, and artifact routes for queued document jobs."""
from __future__ import annotations

from fastapi import APIRouter

from ..core.errors import PdfError
from ..core.responses import file_response
from ..services.jobs import job_manager

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@router.get("/{job_id}", summary="查询任务状态")
async def get_job(job_id: str):
    return job_manager.serialize(job_manager.get(job_id))


@router.post("/{job_id}/cancel", summary="取消任务")
async def cancel_job(job_id: str):
    job = await job_manager.cancel(job_id)
    if job.state == "cancelled":
        return {"jobId": job.id, "state": job.state}
    if job.state == "succeeded":
        raise PdfError(409, "任务已提交，无法取消", code="JOB_COMMITTING")
    return {"jobId": job.id, "state": job.state}


@router.get("/{job_id}/result", summary="下载任务结果")
async def get_job_result(job_id: str):
    path, filename, media_type = job_manager.artifact(job_id)
    return file_response(path, filename, media_type=media_type, delete_after=False)
