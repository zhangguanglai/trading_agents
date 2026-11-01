"""Portfolio import / parse-image / overview router."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from api.database import UserDB, get_db
from api.dependencies import _require_api_user
from api.main import (
    PortfolioImportSyncRequest,
    PortfolioOverviewResponse,
    _annotate_scheduled_with_imported_context,
    _attach_stock_names,
    _get_reverse_stock_map_cached_only,
)
from api.services import portfolio_import_service, report_service, scheduled_service, watchlist_service

router = APIRouter(tags=["portfolio"])


@router.get("/v1/portfolio/imports")
def get_portfolio_import_state(
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    return portfolio_import_service.get_import_state(db, current_user.id)


@router.post("/v1/portfolio/imports")
def sync_portfolio_import(
    body: PortfolioImportSyncRequest,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    try:
        return portfolio_import_service.sync_positions(
            db=db,
            user_id=current_user.id,
            positions=[p.model_dump() for p in body.positions],
            source=body.source,
            auto_apply_scheduled=body.auto_apply_scheduled,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/v1/portfolio/imports", status_code=204)
def clear_portfolio_import_state(
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    portfolio_import_service.clear_imported_portfolio(db, current_user.id)


@router.post("/v1/portfolio/parse-image")
async def parse_position_image_endpoint(
    file: UploadFile = File(...),
    current_user: UserDB = Depends(_require_api_user),
):
    """Parse a broker position screenshot using server-side VLM."""
    import logging
    from api.services.vlm_position_parser import parse_position_image

    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "只支持图片文件")

    image_bytes = await file.read()
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(400, "图片不能超过 10MB")

    try:
        positions = await asyncio.to_thread(parse_position_image, image_bytes, file.content_type)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logging.getLogger(__name__).warning("[parse-image] VLM parsing failed: %s", exc)
        raise HTTPException(500, "图片解析失败，请稍后重试") from exc

    return {"positions": positions}


@router.get("/v1/portfolio/overview", response_model=PortfolioOverviewResponse)
def get_portfolio_overview(
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    code_to_name = _get_reverse_stock_map_cached_only()

    watchlist_items = watchlist_service.list_watchlist(db, current_user.id)
    _attach_stock_names(watchlist_items, code_to_name)

    scheduled_items = scheduled_service.list_scheduled(db, current_user.id)
    _attach_stock_names(scheduled_items, code_to_name)
    scheduled_items = _annotate_scheduled_with_imported_context(scheduled_items, db, current_user.id)

    latest_reports = report_service.get_latest_reports_by_symbols(
        db=db,
        user_id=current_user.id,
        symbols=[item["symbol"] for item in watchlist_items],
    )
    for report in latest_reports:
        report.name = code_to_name.get(report.symbol, report.symbol)

    portfolio_import = portfolio_import_service.get_import_state(db, current_user.id)

    return {
        "watchlist": watchlist_items,
        "scheduled": scheduled_items,
        "latest_reports": latest_reports,
        "portfolio_import": portfolio_import,
    }
