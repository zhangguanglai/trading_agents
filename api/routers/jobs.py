"""Job status / result / event-stream router."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from api.database import UserDB, get_db
from api.dependencies import _require_api_user
from api.main import JobStatusResponse, _get_job, _require_job_owner, _stream_job_events

router = APIRouter(tags=["jobs"])


@router.get("/v1/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str, current_user: UserDB = Depends(_require_api_user)) -> JobStatusResponse:
    job = _require_job_owner(job_id, current_user)
    return JobStatusResponse(
        job_id=job["job_id"],
        status=job["status"],
        created_at=job["created_at"],
        started_at=job.get("started_at"),
        finished_at=job.get("finished_at"),
        symbol=job["symbol"],
        trade_date=job["trade_date"],
        error=job.get("error"),
        waiting_ahead_count=job.get("waiting_ahead_count"),
        scheduled_running_count=job.get("scheduled_running_count"),
        scheduled_concurrency_limit=job.get("scheduled_concurrency_limit"),
    )


@router.get("/v1/jobs/{job_id}/result")
def get_job_result(job_id: str, current_user: UserDB = Depends(_require_api_user)):
    job = _require_job_owner(job_id, current_user)
    if job["status"] != "completed":
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail=f"job status is {job['status']}")
    return {
        "job_id": job_id,
        "status": job["status"],
        "decision": job.get("decision"),
        "result": job.get("result"),
        "finished_at": job.get("finished_at"),
    }


@router.get("/v1/jobs/{job_id}/events")
def stream_job_events(job_id: str, current_user: UserDB = Depends(_require_api_user)):
    _require_job_owner(job_id, current_user)
    return StreamingResponse(
        _stream_job_events(job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
