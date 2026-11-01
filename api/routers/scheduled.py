"""Scheduled analysis router."""

from __future__ import annotations

from typing import Any, Dict, List
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import api.main as _main
from api.database import UserDB, get_db
from api.dependencies import _require_api_user
from api.main import (
    AnalyzeResponse,
    BatchScheduledTriggerResponse,
    ScheduledBatchIdsRequest,
    ScheduledBatchUpdateRequest,
    _annotate_scheduled_with_imported_context,
    _attach_stock_names,
    _build_manual_imported_user_context,
    _build_scheduled_analyze_request,
    _emit_job_event,
    _extract_scheduled_update_kwargs,
    _get_reverse_stock_map_cached_only,
    _set_job,
    _utcnow_iso,
)
from api.services import scheduled_service
from tradingagents.dataflows.trade_calendar import cn_today_str

router = APIRouter(tags=["scheduled"])


@router.get("/v1/scheduled")
def list_scheduled_analyses(
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    items = scheduled_service.list_scheduled(db, current_user.id)
    _attach_stock_names(items, _get_reverse_stock_map_cached_only())
    return {"items": _annotate_scheduled_with_imported_context(items, db, current_user.id)}


@router.post("/v1/scheduled", status_code=201)
def create_scheduled_analysis(
    body: dict,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    symbol = body.get("symbol", "").strip().upper()
    horizon = body.get("horizon", "short")
    trigger_time = body.get("trigger_time", "20:00")
    if not symbol:
        raise HTTPException(400, "symbol is required")
    code_to_name = _main._get_reverse_stock_map()
    if symbol not in code_to_name:
        raise HTTPException(400, f"未知的股票代码: {symbol}")
    try:
        item = scheduled_service.create_scheduled(db, current_user.id, symbol, horizon, trigger_time)
        item["name"] = code_to_name.get(symbol, symbol)
        _annotate_scheduled_with_imported_context([item], db, current_user.id)
        return item
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.patch("/v1/scheduled/batch")
def batch_update_scheduled_analyses(
    body: ScheduledBatchUpdateRequest,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    kwargs = _extract_scheduled_update_kwargs(body.model_dump(exclude_unset=True))
    if not kwargs:
        raise HTTPException(400, "至少提供一个更新字段")
    try:
        items = scheduled_service.batch_update_scheduled(
            db,
            current_user.id,
            body.item_ids,
            **kwargs,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    code_to_name = _main._get_reverse_stock_map()
    for item in items:
        item["name"] = code_to_name.get(item["symbol"], item["symbol"])
    return {"items": _annotate_scheduled_with_imported_context(items, db, current_user.id)}


@router.post("/v1/scheduled/batch/delete")
def batch_delete_scheduled_analyses(
    body: ScheduledBatchIdsRequest,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    try:
        return scheduled_service.batch_delete_scheduled(db, current_user.id, body.item_ids)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/v1/scheduled/batch/trigger", response_model=BatchScheduledTriggerResponse)
async def trigger_scheduled_analyses_batch(
    body: ScheduledBatchIdsRequest,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    if not body.item_ids:
        raise HTTPException(400, "请至少选择 1 个定时任务")

    requested_trade_date = cn_today_str()
    actual_trade_date = _main._resolve_scheduled_trade_date(requested_trade_date)
    code_to_name = _main._get_reverse_stock_map()
    jobs: List[Dict[str, Any]] = []
    with_position_context = 0
    available_tasks = {
        task["id"]: task
        for task in scheduled_service.list_scheduled(db, current_user.id)
    }
    valid_item_ids = []
    missing_item_ids = []
    for raw_item_id in body.item_ids:
        item_id = str(raw_item_id or "").strip()
        if not item_id:
            continue
        if item_id in available_tasks:
            valid_item_ids.append(item_id)
        else:
            missing_item_ids.append(item_id)

    if not valid_item_ids:
        raise HTTPException(400, "选中的定时任务已失效，请刷新页面后重试")

    if missing_item_ids:
        from api.main import _log
        _log(
            f"[Scheduled Batch Trigger] user={current_user.id} skipped missing item_ids={missing_item_ids}"
        )

    for item_id in valid_item_ids:
        task = available_tasks[item_id]

        task_snapshot = dict(task)
        task_snapshot["user_id"] = current_user.id
        task_snapshot["manual_user_context"] = _build_manual_imported_user_context(db, current_user.id, task["symbol"])

        scheduled_user_context = task_snapshot["manual_user_context"]
        if scheduled_user_context.get("current_position") is not None:
            with_position_context += 1

        now = _utcnow_iso()
        job_id = uuid4().hex
        _set_job(
            job_id,
            job_id=job_id,
            status="pending",
            created_at=now,
            symbol=task["symbol"],
            trade_date=actual_trade_date,
            user_id=current_user.id,
            request_source="scheduled_manual_batch",
        )
        _emit_job_event(
            job_id,
            "job.queued",
            {"job_id": job_id, "symbol": task["symbol"], "trade_date": actual_trade_date},
        )
        _main._create_tracked_task(
            _main._run_scheduled_analysis_once(
                task_snapshot,
                requested_trade_date,
                job_id,
            )
        )

        jobs.append({
            "item_id": task["id"],
            "job_id": job_id,
            "symbol": task["symbol"],
            "name": code_to_name.get(task["symbol"], task["symbol"]),
            "status": "pending",
            "created_at": now,
            "current_position": scheduled_user_context.get("current_position"),
            "average_cost": scheduled_user_context.get("average_cost"),
        })

    return {
        "summary": {
            "total": len(jobs),
            "with_position_context": with_position_context,
        },
        "jobs": jobs,
    }


@router.post("/v1/scheduled/{item_id}/trigger", response_model=AnalyzeResponse)
async def trigger_scheduled_analysis_once(
    item_id: str,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    task = scheduled_service.get_scheduled(db, current_user.id, item_id)
    if task is None:
        raise HTTPException(404, "未找到该定时任务")

    requested_trade_date = cn_today_str()
    actual_trade_date = _main._resolve_scheduled_trade_date(requested_trade_date)
    now = _utcnow_iso()
    job_id = uuid4().hex

    task_snapshot = dict(task)
    task_snapshot["user_id"] = current_user.id
    task_snapshot["manual_user_context"] = _build_manual_imported_user_context(db, current_user.id, task["symbol"])

    _set_job(
        job_id,
        job_id=job_id,
        status="pending",
        created_at=now,
        symbol=task["symbol"],
        trade_date=actual_trade_date,
        user_id=current_user.id,
        request_source="scheduled_manual",
    )
    _emit_job_event(
        job_id,
        "job.queued",
        {"job_id": job_id, "symbol": task["symbol"], "trade_date": actual_trade_date},
    )
    _main._create_tracked_task(
        _main._run_scheduled_analysis_once(
            task_snapshot,
            requested_trade_date,
            job_id,
        )
    )
    return AnalyzeResponse(job_id=job_id, status="pending", created_at=now)


@router.patch("/v1/scheduled/{item_id}")
def update_scheduled_analysis(
    item_id: str,
    body: dict,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    kwargs = _extract_scheduled_update_kwargs(body)
    try:
        result = scheduled_service.update_scheduled(db, current_user.id, item_id, **kwargs)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if result is None:
        raise HTTPException(404, "未找到该定时任务")
    code_to_name = _main._get_reverse_stock_map()
    result["name"] = code_to_name.get(result["symbol"], result["symbol"])
    _annotate_scheduled_with_imported_context([result], db, current_user.id)
    return result


@router.delete("/v1/scheduled/{item_id}", status_code=204)
def delete_scheduled_analysis(
    item_id: str,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    if not scheduled_service.delete_scheduled(db, current_user.id, item_id):
        raise HTTPException(404, "未找到该定时任务")
