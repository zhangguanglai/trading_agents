"""Miscellaneous router: healthz, version-stats, backtest, sponsors."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

import api.main as _api_main
from api.database import VersionStatsDB, get_db
from api.dependencies import _get_real_ip, _require_api_user
from api.scheduler_heartbeat import scheduler_heartbeat_status
from api.main import (
    BacktestRequest,
    SponsorItem,
    SponsorsResponse,
)
from api.services import backtest_service as _bt, sponsor_service

router = APIRouter(tags=["misc"])

# Simple in-memory rate limiter for version stats: {ip: last_timestamp}
_vs_rate_limit: Dict[str, float] = {}
_VS_RATE_INTERVAL = 3600  # at most once per hour per IP


@router.get("/healthz")
async def healthz():
    """健康检查，同时探测 asyncio 默认线程池是否被僵尸线程占满。

    向默认 executor 提交一个 no-op，5 秒内排不上队即判定饱和并返回 503
    （生产事故：无超时的网络调用把 worker 全部占死，所有依赖 to_thread
    的接口静默挂起，前端表现为网关超时）。
    """
    payload: Dict[str, Any] = {"status": "ok"}
    executor = _api_main._default_executor
    if executor is not None:
        payload["executor_queued"] = executor._work_queue.qsize()
        payload["executor_threads"] = len(executor._threads)
    try:
        loop = asyncio.get_running_loop()
        await asyncio.wait_for(loop.run_in_executor(None, int), timeout=5)
    except asyncio.TimeoutError:
        payload["status"] = "thread_pool_starved"
        return JSONResponse(status_code=503, content=payload)
    return payload


@router.get("/readyz")
def readyz(db: Session = Depends(get_db)):
    """Report whether the API can serve traffic and its required peers are ready."""
    checks: Dict[str, Any] = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = {"status": "ok"}
    except Exception as exc:
        checks["database"] = {"status": "error", "error": type(exc).__name__}

    redis_url = os.getenv("REDIS_URL", "").strip()
    if redis_url:
        redis_client = None
        try:
            import redis

            redis_client = redis.Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
            redis_client.ping()
            checks["redis"] = {"status": "ok"}
        except Exception as exc:
            checks["redis"] = {"status": "error", "error": type(exc).__name__}
        finally:
            if redis_client is not None:
                redis_client.close()

    if os.getenv("TA_REQUIRE_SCHEDULER", "").strip().lower() in {"1", "true", "yes", "on"}:
        checks["scheduler"] = scheduler_heartbeat_status()

    is_ready = all(check["status"] == "ok" for check in checks.values())
    payload = {"status": "ok" if is_ready else "not_ready", "checks": checks}
    return JSONResponse(status_code=200 if is_ready else 503, content=payload)


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
    current_user=Depends(_require_api_user),
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

    job = _bt.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="回测任务不存在")
    return job


@router.delete("/v1/backtest/{job_id}")
def delete_backtest(job_id: str) -> Dict:
    """删除回测任务."""

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
