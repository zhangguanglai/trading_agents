"""Market data router (kline, hot-stocks, stock-search)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

import api.main as _main
from api.dependencies import _require_api_user
from api.main import (
    KlineResponse,
    _build_runtime_config,
    _fetch_index_kline,
    _is_cn_index_symbol,
    _log,
    _normalize_symbol,
    _normalize_ths_code,
    _parse_stock_csv,
    _search_cn_stock_by_name,
)
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.interface import route_to_vendor
from tradingagents.dataflows.trade_calendar import cn_today_str

router = APIRouter(tags=["market"])


@router.get("/v1/market/kline", response_model=KlineResponse)
def get_kline(
    symbol: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> KlineResponse:
    end = end_date or cn_today_str()
    if start_date:
        start = start_date
    else:
        start = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=120)).strftime("%Y-%m-%d")

    if _is_cn_index_symbol(symbol):
        candles = _fetch_index_kline(symbol, start, end)
    else:
        # Normalize symbol (convert "阳光电源" -> "300274.SZ")
        symbol = _normalize_symbol(symbol)
        config = _build_runtime_config({})
        set_config(config)
        raw = route_to_vendor("get_stock_data", symbol, start, end)
        candles = _parse_stock_csv(raw)
    if not candles:
        raise HTTPException(status_code=404, detail="no kline data")
    return KlineResponse(
        symbol=symbol,
        start_date=start,
        end_date=end,
        candles=candles,
    )


@router.get("/v1/market/hot-stocks")
def get_hot_stocks(source: str = "em", limit: int = 30) -> Dict:
    """Return hot A-share stocks from different sources.
    
    Args:
        source: Data source selection
            - 'em': 东方财富热榜 (EastMoney hot stocks)
            - 'xq': 雪球热门 (Xueqiu most-followed stocks)
            - 'ths': 连涨榜 (Consecutive rising stocks, not general hot list)
        limit: Maximum number of stocks to return
    
    Returns:
        Dict with stocks list, total count, source info, and fallback status
    """
    import akshare as ak

    # 定义数据源尝试顺序（如果主数据源失败，自动尝试备用源）
    source_configs = {
        "em": ("stock_hot_rank_em", None, "东方财富热榜"),
        "xq": ("stock_hot_follow_xq", "最热门", "雪球热门"),
        "ths": ("stock_rank_lxsz_ths", None, "连涨榜"),
    }

    if source not in source_configs:
        raise HTTPException(status_code=400, detail=f"Unknown source: {source}")

    # 尝试主数据源，失败则尝试其他源
    sources_to_try = [source] + [s for s in ["xq", "em", "ths"] if s != source]
    last_error = None

    for src in sources_to_try:
        try:
            func_name, param, desc = source_configs[src]
            func = getattr(ak, func_name)

            # 调用 akshare 函数
            if param:
                df = func(symbol=param).head(limit)
            else:
                df = func().head(limit)

            stocks = []

            if src == "em":
                for i, (_, row) in enumerate(df.iterrows()):
                    stocks.append({
                        "rank": i + 1,
                        "symbol": _normalize_ths_code(str(row.get("代码", ""))),
                        "name": str(row.get("股票名称", "")),
                        "price": float(row.get("最新价", 0) or 0),
                        "change": float(row.get("涨跌额", 0) or 0),
                        "change_pct": float(row.get("涨跌幅", 0) or 0),
                        "extra": "",
                    })

            elif src == "xq":
                for i, (_, row) in enumerate(df.iterrows()):
                    stocks.append({
                        "rank": i + 1,
                        "symbol": _normalize_ths_code(str(row.get("股票代码", ""))),
                        "name": str(row.get("股票简称", "")),
                        "price": float(row.get("最新价", 0) or 0),
                        "change": 0.0,
                        "change_pct": 0.0,
                        "extra": f"关注 {int(row.get('关注', 0)):,}",
                    })

            elif src == "ths":
                for i, (_, row) in enumerate(df.iterrows()):
                    days = int(row.get("连涨天数", 0) or 0)
                    change_pct = float(row.get("连续涨跌幅", 0) or 0)
                    stocks.append({
                        "rank": i + 1,
                        "symbol": _normalize_ths_code(str(row.get("股票代码", ""))),
                        "name": str(row.get("股票简称", "")),
                        "price": float(row.get("收盘价", 0) or 0),
                        "change": 0.0,
                        "change_pct": change_pct,
                        "extra": f"连涨{days}天",
                    })

            # 成功获取数据
            fallback_msg = f" (fallback from {source_configs[source][2]})" if src != source else ""
            _log(f"Hot stocks: successfully fetched from {desc}{fallback_msg}")
            return {
                "stocks": stocks,
                "total": len(stocks),
                "source": src,
                "requested_source": source,
                "fallback": src != source,
            }

        except Exception as e:
            last_error = e
            _log(f"Hot stocks: {desc} failed - {type(e).__name__}: {str(e)[:100]}")
            continue

    # 所有数据源都失败
    raise HTTPException(
        status_code=503,
        detail=f"All data sources failed. Last error: {type(last_error).__name__}: {str(last_error)[:200]}"
    )


@router.get("/v1/market/stock-search")
def search_stocks(
    q: str = Query("", min_length=1, max_length=20),
    current_user = Depends(_require_api_user),
):
    """Search stocks by code prefix or name substring."""
    q = q.strip()
    if not q:
        return {"results": []}

    name_to_code = _main._load_cn_stock_map()
    code_to_name = _main._get_reverse_stock_map()
    results = []
    q_upper = q.upper()

    for code, name in code_to_name.items():
        if code.upper().startswith(q_upper) or code.split(".")[0].startswith(q):
            results.append({"symbol": code, "name": name})
            if len(results) >= 20:
                break

    if len(results) < 20:
        for name, code in name_to_code.items():
            if q in name and not any(r["symbol"] == code for r in results):
                results.append({"symbol": code, "name": name})
                if len(results) >= 20:
                    break

    return {"results": results}
