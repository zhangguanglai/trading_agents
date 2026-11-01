"""Watchlist router."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import api.main as _main
from api.database import UserDB, get_db
from api.dependencies import _require_api_user
from api.main import (
    WatchlistAddRequest,
    _attach_stock_names,
    _resolve_watchlist_identifier,
    _split_watchlist_batch_text,
)
from api.services import watchlist_service

router = APIRouter(tags=["watchlist"])


@router.get("/v1/watchlist")
def list_watchlist(
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    items = watchlist_service.list_watchlist(db, current_user.id)
    _attach_stock_names(items, _main._get_reverse_stock_map())
    return {"items": items}


@router.post("/v1/watchlist")
def add_to_watchlist(
    body: WatchlistAddRequest,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    text = str(body.text or body.symbol or "").strip()
    if not text:
        raise HTTPException(400, "text or symbol is required")

    tokens = _split_watchlist_batch_text(text)
    if not tokens:
        raise HTTPException(400, "至少提供一个股票代码或名称")

    name_to_code = _main._load_cn_stock_map()
    code_to_name = _main._get_reverse_stock_map()

    resolved_entries: List[Dict[str, Any]] = []
    results: List[Dict[str, Any]] = []
    for idx, token in enumerate(tokens):
        symbol, name, error = _resolve_watchlist_identifier(token, name_to_code, code_to_name)
        if error:
            results.append({
                "_order": idx,
                "input": token,
                "status": "invalid",
                "message": error,
            })
            continue
        resolved_entries.append({
            "_order": idx,
            "input": token,
            "symbol": symbol,
            "name": name,
        })

    add_results = watchlist_service.add_watchlist_items(
        db,
        current_user.id,
        [entry["symbol"] for entry in resolved_entries],
    )
    for entry, result in zip(resolved_entries, add_results):
        item = result.get("item")
        if item:
            item["name"] = entry["name"]
            item["has_scheduled"] = False
        results.append({
            "_order": entry["_order"],
            "input": entry["input"],
            "symbol": entry["symbol"],
            "name": entry["name"],
            "status": result["status"],
            "message": result["message"],
            "item": item,
        })

    results.sort(key=lambda row: row["_order"])
    for row in results:
        row.pop("_order", None)
    summary = {
        "total": len(tokens),
        "added": sum(1 for row in results if row["status"] == "added"),
        "duplicate": sum(1 for row in results if row["status"] == "duplicate"),
        "failed": sum(1 for row in results if row["status"] in {"invalid", "failed"}),
    }
    message_parts = [f"共处理 {summary['total']} 项"]
    if summary["added"]:
        message_parts.append(f"新增 {summary['added']} 项")
    if summary["duplicate"]:
        message_parts.append(f"重复 {summary['duplicate']} 项")
    if summary["failed"]:
        message_parts.append(f"失败 {summary['failed']} 项")
    return {
        "message": "，".join(message_parts),
        "summary": summary,
        "results": results,
    }


@router.delete("/v1/watchlist/{item_id}", status_code=204)
def delete_from_watchlist(
    item_id: str,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    if not watchlist_service.delete_watchlist_item(db, current_user.id, item_id):
        raise HTTPException(404, "未找到该自选股")
