"""Reports & announcements router."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.database import UserDB, get_db
from api.dependencies import _require_api_user
from api.main import (
    LatestAnnouncementResponse,
    LatestReportsBySymbolsRequest,
    LatestReportsBySymbolsResponse,
    ReportBatchDeleteRequest,
    ReportBatchDeleteResponse,
    ReportCreateRequest,
    ReportDetailResponse,
    ReportListResponse,
    ReportResponse,
    _attach_job_runtime_state,
    _get_job,
    _get_reverse_stock_map,
    _get_reverse_stock_map_cached_only,
    _load_latest_announcement,
)
from api.services import report_service

router = APIRouter(tags=["reports"])


@router.post("/v1/reports", response_model=ReportResponse)
def create_report_endpoint(
    request: ReportCreateRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(_require_api_user),
):
    """手动创建报告（通常由系统自动调用）."""
    report = report_service.create_report(
        db=db,
        symbol=request.symbol,
        trade_date=request.trade_date,
        decision=request.decision,
        result_data=request.result_data,
        user_id=current_user.id,
    )
    return report


@router.get("/v1/announcements/latest", response_model=LatestAnnouncementResponse)
def get_latest_announcement():
    return {"announcement": _load_latest_announcement()}


@router.get("/v1/reports", response_model=ReportListResponse)
def list_reports(
    symbol: Optional[str] = Query(None, description="按股票代码筛选"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(_require_api_user),
):
    """获取报告列表."""
    total = report_service.count_reports(db=db, user_id=current_user.id, symbol=symbol)
    reports = report_service.get_reports_by_user(
        db=db,
        user_id=current_user.id,
        symbol=symbol,
        skip=skip,
        limit=limit,
    )
    code_to_name = _get_reverse_stock_map_cached_only()
    for r in reports:
        r.name = code_to_name.get(r.symbol, r.symbol)
        _attach_job_runtime_state(r, str(getattr(r, "id", "")))
    return {"total": total, "reports": reports}


@router.post("/v1/reports/latest-by-symbols", response_model=LatestReportsBySymbolsResponse)
def list_latest_reports_by_symbols(
    body: LatestReportsBySymbolsRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(_require_api_user),
):
    reports = report_service.get_latest_reports_by_symbols(
        db=db,
        user_id=current_user.id,
        symbols=body.symbols,
    )
    return {"reports": reports}


@router.get("/v1/reports/{report_id}", response_model=ReportDetailResponse)
def get_report_endpoint(
    report_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(_require_api_user),
):
    """获取报告详情."""
    report = report_service.get_report(db, report_id, user_id=current_user.id)
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    if str(report.status or "") in report_service.ACTIVE_REPORT_STATUSES and not _get_job(report_id):
        report = report_service.finalize_orphan_report(db, report)
    code_to_name = _get_reverse_stock_map()
    report.name = code_to_name.get(report.symbol, report.symbol)
    _attach_job_runtime_state(report, report_id)
    return report


@router.delete("/v1/reports/{report_id}")
def delete_report_endpoint(
    report_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(_require_api_user),
):
    """删除报告."""
    success = report_service.delete_report(db, report_id, user_id=current_user.id)
    if not success:
        raise HTTPException(status_code=404, detail="报告不存在")
    return {"message": "报告已删除"}


@router.post("/v1/reports/batch/delete", response_model=ReportBatchDeleteResponse)
def batch_delete_reports_endpoint(
    body: ReportBatchDeleteRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(_require_api_user),
):
    try:
        return report_service.batch_delete_reports(db, body.report_ids, user_id=current_user.id)
    except ValueError as e:
        raise HTTPException(400, str(e))
