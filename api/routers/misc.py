"""Miscellaneous router: healthz, version-stats, backtest, sponsors."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Request
from sqlalchemy.orm import Session

from api.database import VersionStatsDB, get_db
from api.dependencies import _get_real_ip, _require_api_user
from api.main import (
    BacktestRequest,
    SponsorItem,
    SponsorsResponse,
    _report_version_stats,
)
from api.services import backtest_service as _bt, sponsor_service

router = APIRouter(tags=["misc"])

# Simple in-memory rate limiter for version stats: {ip: last_timestamp}
_vs_rate_limit: Dict[str, float] = {}
_VS_RATE_INTERVAL = 3600  # at most once per hour per IP


@router.get("/healthz")
def healthz() -> Dict[str, str]:
    return {"status": "ok"}


@router.post("/api/version-stats")
def version_stats(payload: Dict[str, Any] = Body(...), request: Request = None, db: Session = Depends(get_db)):
    """Collect anonymous version statistics from deployed instances."""
    remote_ip = _get_real_ip(request)

    # Rate limit by IP
    now = time.time()
    if remote_ip:
        last = _vs_rate_limit.get(remote_ip, 0)
        if now - last < _VS_RATE_INTERVAL:
            return {"status": "ok"}
        _vs_rate_limit[remote_ip] = now

    record = VersionStatsDB(
        version=str(payload.get("v", ""))[:50],
        nonce=str(payload.get("nonce", ""))[:64],
        remote_ip=remote_ip,
    )
    db.add(record)
    db.commit()
    return {"status": "ok"}


# ─── Backtest Endpoints ───────────────────────────────────────────────────────


@router.post("/v1/backtest")
def submit_backtest(
    request: BacktestRequest,
    db: Session = Depends(get_db),
    current_user = Depends(_require_api_user),
) -> Dict:
    """提交历史回测任务，返回 job_id."""
    from api.main import _build_runtime_config
    config = _build_runtime_config(request.config_overrides or {}, user_id=current_user.id, db=db)
    job_id = _bt.submit(
        symbol=request.symbol,
        start_date=request.start_date,
        end_date=request.end_date,
        selected_analysts=request.selected_analysts,
        hold_days=request.hold_days,
        sample_interval=request.sample_interval,
        config=config,
    )
    return {"job_id": job_id, "status": "pending"}


@router.get("/v1/backtest")
def list_backtests() -> Dict:
    """列出所有回测任务."""
    jobs = _bt.list_jobs()
    return {"jobs": jobs, "total": len(jobs)}


@router.get("/v1/backtest/{job_id}")
def get_backtest(job_id: str) -> Dict:
    """获取回测任务状态和结果."""
    from fastapi import HTTPException
    job = _bt.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="回测任务不存在")
    return job


@router.delete("/v1/backtest/{job_id}")
def delete_backtest(job_id: str) -> Dict:
    """删除回测任务."""
    from fastapi import HTTPException
    if not _bt.delete_job(job_id):
        raise HTTPException(status_code=404, detail="回测任务不存在")
    return {"message": "已删除"}


# ─── Sponsor endpoints (public, no auth) ────────────────────────────────────


def _sponsor_to_item(s: Any) -> SponsorItem:
    return SponsorItem(
        id=s.id,
        sponsor_type=s.sponsor_type,
        name=s.name,
        github=s.github,
        avatar=s.avatar,
        email=s.email,
        provider=s.provider,
        date=s.date,
    )


@router.get("/v1/sponsors", response_model=SponsorsResponse)
def list_sponsors(db: Session = Depends(get_db)):
    """Public endpoint: list all visible sponsors grouped by type."""
    all_sponsors = sponsor_service.list_sponsors(db)
    money = [_sponsor_to_item(s) for s in all_sponsors if s.sponsor_type == "money"]
    token = [_sponsor_to_item(s) for s in all_sponsors if s.sponsor_type == "token"]
    return SponsorsResponse(money=money, token=token)


def mount_static_files(app: FastAPI) -> None:
    """Mount uploads/frontend static directories if they exist."""
    import os
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    # Serve uploaded files (avatars etc.) from shared uploads directory
    _uploads_dir = Path(os.getenv("UPLOAD_DIR", str(Path(__file__).parent.parent.parent / "uploads")))
    if _uploads_dir.is_dir():
        app.mount("/uploads", StaticFiles(directory=str(_uploads_dir)), name="uploads")

    # Mount frontend if dist exists
    dist_path = os.path.join(os.getcwd(), "frontend/dist")
    if os.path.exists(dist_path):
        app.mount("/assets", StaticFiles(directory=os.path.join(dist_path, "assets")), name="assets")

        @app.get("/{full_path:path}")
        async def serve_frontend(full_path: str):
            # 1. Define and resolve the absolute safe root
            base_path = os.path.realpath(dist_path)
            
            # 2. Resolve the requested path (handling .. and symlinks)
            fullpath = os.path.realpath(os.path.join(base_path, full_path.lstrip("/")))
            
            # 3. Security Check: The normalized path must start with the base_path
            if not fullpath.startswith(base_path):
                return FileResponse(os.path.join(base_path, "index.html"))
                
            # 4. Final check: if it's a valid file, serve it
            if os.path.isfile(fullpath):
                return FileResponse(fullpath)
                
            # Otherwise fallback to index.html for SPA routing
            return FileResponse(os.path.join(base_path, "index.html"))
