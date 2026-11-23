"""A-share stock name↔code mapping and symbol normalization utilities.

Extracted from ``api.main`` to keep the routing module focused on app
assembly. All symbols are re-exported from ``api.main`` so existing
imports continue to work.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time as _time
from io import StringIO
from threading import Lock
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stock map cache (name→code and code→name)
# ---------------------------------------------------------------------------
_cn_stock_map: Optional[Dict[str, str]] = None
_cn_stock_reverse_map: Optional[Dict[str, str]] = None
_cn_stock_map_lock = Lock()
_cn_stock_map_loaded_at: float = 0  # timestamp of last load
_STOCK_MAP_TTL = 7 * 86400  # 7 days


def _load_cn_stock_map() -> Dict[str, str]:
    """Lazy-load and cache A-share stock + ETF/fund name→code mapping (7-day TTL).

    Uses akshare stock_info_a_code_name (static list, no anti-crawl) for A-shares,
    plus fund_name_em for ETFs/funds.

    Optimized: loads from local cache file first, falls back to AKShare only on cache miss.
    """
    global _cn_stock_map, _cn_stock_reverse_map, _cn_stock_map_loaded_at
    now = _time.time()
    if _cn_stock_map is not None and (now - _cn_stock_map_loaded_at) > _STOCK_MAP_TTL:
        _cn_stock_map = None  # expire cache
        _cn_stock_reverse_map = None
    if _cn_stock_map is not None:
        return _cn_stock_map
    with _cn_stock_map_lock:
        if _cn_stock_map is not None and (now - _cn_stock_map_loaded_at) <= _STOCK_MAP_TTL:
            return _cn_stock_map

        result: Dict[str, str] = {}
        cache_file = os.path.join(os.path.dirname(__file__), "..", "data", "stock_map_cache.json")
        cache_file = os.path.abspath(cache_file)

        # 1. Try loading from local cache file first (fast, no network)
        try:
            if os.path.exists(cache_file):
                file_mtime = os.path.getmtime(cache_file)
                if (now - file_mtime) <= _STOCK_MAP_TTL:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        cached = json.load(f)
                    result = cached.get("map", {})
                    _cn_stock_map = result
                    _cn_stock_reverse_map = {code: name for name, code in result.items()}
                    _cn_stock_map_loaded_at = now
                    logger.info(f"[StockMap] Loaded {len(result)} entries from local cache ({cache_file}).")
                    return result
        except Exception as ce:
            logger.info(f"[StockMap] Cache load failed: {ce}")

        # 2. Fallback to AKShare (slow, network-dependent)
        try:
            import akshare as ak

            # A-share stocks (static list, no anti-crawl issue)
            df = ak.stock_info_a_code_name()
            for _, row in df.iterrows():
                name = str(row.get("name", "")).strip()
                code = str(row.get("code", "")).strip()
                if name and code:
                    result[name] = _normalize_symbol(code)
            stock_count = len(result)
            # ETF / funds
            fund_count = 0
            try:
                fund_df = ak.fund_name_em()
                existing_codes = set(result.values())
                for _, row in fund_df.iterrows():
                    code = str(row.get("基金代码", "")).strip()
                    name = str(row.get("基金简称", "")).strip()
                    if name and code and len(code) == 6 and code.isdigit():
                        normalized = _normalize_symbol(code)
                        if normalized not in existing_codes:
                            result[name] = normalized
                            existing_codes.add(normalized)
                fund_count = len(result) - stock_count
            except Exception as fe:
                logger.info(f"[StockMap] ETF/fund load skipped: {fe}")

            # Save to local cache file for next time
            try:
                os.makedirs(os.path.dirname(cache_file), exist_ok=True)
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump({"map": result, "loaded_at": now}, f, ensure_ascii=False)
            except Exception as se:
                logger.info(f"[StockMap] Cache save failed: {se}")

            _cn_stock_map = result
            _cn_stock_reverse_map = {code: name for name, code in result.items()}
            _cn_stock_map_loaded_at = now
            logger.info(
                f"[StockMap] Loaded {stock_count} stocks + {fund_count} ETFs/funds = {len(result)} total from AKShare."
            )
        except Exception as e:
            logger.info(f"[StockMap] Failed to load: {e}")
            if _cn_stock_map is None:
                _cn_stock_map = {}
                _cn_stock_reverse_map = {}
    return _cn_stock_map


def _get_reverse_stock_map() -> Dict[str, str]:
    """Return code→name mapping."""
    _load_cn_stock_map()
    return dict(_cn_stock_reverse_map or {})


def _get_reverse_stock_map_cached_only() -> Dict[str, str]:
    """Return code→name mapping only from already-warmed cache.

    For list pages we prefer a fast response over blocking on a cold AkShare lookup.
    When the cache is cold we simply return an empty mapping and let the UI fall back
    to stock codes. Search endpoints can still call _load_cn_stock_map() explicitly.
    """
    if _cn_stock_map is None or _cn_stock_reverse_map is None:
        return {}
    return dict(_cn_stock_reverse_map)


def _search_cn_stock_by_name(query: str) -> Optional[str]:
    """Look up A-share stock code by company name (exact then partial match)."""
    query = query.strip()
    if not query:
        return None
    stock_map = _load_cn_stock_map()
    # 1. Exact match
    if query in stock_map:
        return stock_map[query]
    # 2. Partial match: query is substring of a stock name or vice versa
    candidates = [(name, code) for name, code in stock_map.items() if query in name or name in query]
    if len(candidates) == 1:
        return candidates[0][1]
    # 3. If multiple partial matches, pick the one with shortest name (closest match)
    if candidates:
        candidates.sort(key=lambda x: len(x[0]))
        return candidates[0][1]
    return None


def _split_watchlist_batch_text(text: str) -> List[str]:
    return [token.strip() for token in re.split(r"[\s,，、；;]+", text.strip()) if token.strip()]


def _resolve_watchlist_identifier(
    raw: str,
    name_to_code: Dict[str, str],
    code_to_name: Dict[str, str],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    token = raw.strip()
    if not token:
        return None, None, "输入为空"
    if token in name_to_code:
        symbol = name_to_code[token]
        return symbol, code_to_name.get(symbol, token), None
    symbol = _normalize_symbol(token)
    if symbol in code_to_name:
        return symbol, code_to_name.get(symbol, symbol), None
    return None, None, f"未识别的股票代码或名称: {token}"


def _attach_stock_names(items: List[dict], code_to_name: Dict[str, str]) -> List[dict]:
    for item in items:
        symbol = str(item.get("symbol") or "").upper()
        item["name"] = code_to_name.get(symbol, symbol or item.get("name") or "")
    return items


# ---------------------------------------------------------------------------
# Symbol normalization
# ---------------------------------------------------------------------------
def _normalize_symbol(raw: str) -> str:
    s = raw.strip().upper()
    # Priority 1: Hong Kong stocks (e.g. 0700.HK)
    hk = re.search(r"(\d{4,5})\.HK", s)
    if hk:
        return f"{hk.group(1)}.HK"
    # Priority 2: 6-digit CN stock code
    m = re.search(r"(\d{6})(?:\.(SH|SZ|SS))?", s)
    if m:
        code = m.group(1)
        suffix = m.group(2)
        if suffix:
            if suffix == "SS":
                return f"{code}.SH"
            return f"{code}.{suffix}"
        market = "SH" if code.startswith(("5", "6", "9")) else "SZ"
        return f"{code}.{market}"
    # Fallback: 1-6 letter ticker
    m2 = re.search(r"([A-Z]{1,6}(?:\.[A-Z]{1,3})?)", s)
    if m2:
        return m2.group(1)

    # Final Fallback: Check Chinese Name Map (e.g. "三花智控" -> "002050.SZ")
    stock_map = _load_cn_stock_map()
    if s in stock_map:
        return stock_map[s]

    return s


def _normalize_ths_code(code: str) -> str:
    """Convert THS/XQ code like SH601xxx → 601xxx.SH"""
    code = str(code).strip()
    if code.upper().startswith("SH"):
        return f"{code[2:]}.SH"
    if code.upper().startswith("SZ"):
        return f"{code[2:]}.SZ"
    if code.upper().startswith("BJ") or code.upper().startswith("NQ"):
        return f"{code[2:]}.BJ"
    # Bare 6-digit code — guess exchange
    if code.startswith(("6", "5")):
        return f"{code}.SH"
    if code.startswith(("0", "3", "2")):
        return f"{code}.SZ"
    return code


# ---------------------------------------------------------------------------
# K-line / index helpers
# ---------------------------------------------------------------------------
CN_INDEX_SYMBOL_MAP = {
    "000001.SH": "sh000001",
    "399001.SZ": "sz399001",
    "399006.SZ": "sz399006",
    "000300.SH": "sh000300",
    "000688.SH": "sh000688",
    "000905.SH": "sh000905",
    "000852.SH": "sh000852",
    "899050.BJ": "bj899050",
}


def _is_cn_index_symbol(symbol: str) -> bool:
    return symbol.upper() in CN_INDEX_SYMBOL_MAP


def _parse_stock_csv(raw: str) -> List[Dict[str, Any]]:
    if not raw:
        return []

    # Try JSON first (e.g., from Tushare provider)
    try:
        data = json.loads(raw)
        if isinstance(data, list) and data:
            df = pd.DataFrame(data)
            # Tushare columns: date, open, high, low, close, volume
            col_map = {
                "date": "Date",
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "volume": "Volume",
                "amount": "Amount",
            }
            df = df.rename(columns=col_map)
            required = ["Date", "Open", "High", "Low", "Close"]
            if all(col in df.columns for col in required):
                for col in ["Open", "High", "Low", "Close", "Volume"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
                df = df.dropna(subset=["Date", "Open", "High", "Low", "Close"]).sort_values("Date")
                if not df.empty:
                    candles: List[Dict[str, Any]] = []
                    for _, row in df.iterrows():
                        candles.append(
                            {
                                "date": row["Date"].strftime("%Y-%m-%d"),
                                "open": float(row["Open"]),
                                "high": float(row["High"]),
                                "low": float(row["Low"]),
                                "close": float(row["Close"]),
                                "volume": float(row["Volume"])
                                if "Volume" in df.columns and pd.notna(row.get("Volume"))
                                else None,
                            }
                        )
                    return candles
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback to CSV parsing (e.g., from AKShare or file)
    lines = [ln for ln in raw.splitlines() if ln.strip() and not ln.startswith("#")]
    if not lines:
        return []

    try:
        df = pd.read_csv(StringIO("\n".join(lines)))
    except Exception:
        return []

    if "Date" not in df.columns:
        return []

    rename_map = {k: k.strip() for k in df.columns}
    df = df.rename(columns=rename_map)
    required = ["Date", "Open", "High", "Low", "Close"]
    for col in required:
        if col not in df.columns:
            return []

    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "Open", "High", "Low", "Close"]).sort_values("Date")
    if df.empty:
        return []

    candles: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        candles.append(
            {
                "date": row["Date"].strftime("%Y-%m-%d"),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row["Volume"]) if "Volume" in df.columns and pd.notna(row.get("Volume")) else None,
            }
        )
    return candles


def _normalize_kline_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    col_map = {
        "日期": "Date",
        "date": "Date",
        "Date": "Date",
        "开盘": "Open",
        "open": "Open",
        "Open": "Open",
        "最高": "High",
        "high": "High",
        "High": "High",
        "最低": "Low",
        "low": "Low",
        "Low": "Low",
        "收盘": "Close",
        "close": "Close",
        "Close": "Close",
        "成交量": "Volume",
        "volume": "Volume",
        "Volume": "Volume",
        "成交额": "Amount",
        "amount": "Amount",
        "Amount": "Amount",
        "涨跌幅": "ChangePercent",
        "涨跌额": "Change",
        "换手率": "TurnoverRate",
    }
    out = df.rename(columns=col_map).copy()
    required = ["Date", "Open", "High", "Low", "Close"]
    if any(col not in out.columns for col in required):
        return pd.DataFrame()

    out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
    out = out.dropna(subset=["Date"]).sort_values("Date")
    for col in ["Open", "High", "Low", "Close", "Volume", "Amount", "ChangePercent", "Change", "TurnoverRate"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["Open", "High", "Low", "Close"])
    return out.reset_index(drop=True)


def _fetch_index_kline(symbol: str, start_date: str, end_date: str) -> List[Dict[str, Any]]:
    import akshare as ak  # type: ignore

    symbol_key = symbol.upper()
    vendor_symbol = CN_INDEX_SYMBOL_MAP.get(symbol_key)
    if not vendor_symbol:
        return []

    yyyymmdd_start = start_date.replace("-", "")
    yyyymmdd_end = end_date.replace("-", "")
    last_exc: Exception | None = None

    for fetcher in (
        lambda: ak.stock_zh_index_daily_em(
            symbol=vendor_symbol,
            start_date=yyyymmdd_start,
            end_date=yyyymmdd_end,
        ),
        lambda: ak.stock_zh_index_daily(symbol=vendor_symbol),
        lambda: ak.index_zh_a_hist(
            symbol=symbol_key.split(".")[0],
            period="daily",
            start_date=yyyymmdd_start,
            end_date=yyyymmdd_end,
        ),
    ):
        try:
            raw_df = fetcher()
            df = _normalize_kline_df(raw_df)
            if df.empty:
                continue
            df = df[(df["Date"] >= pd.to_datetime(start_date)) & (df["Date"] <= pd.to_datetime(end_date))]
            if df.empty:
                continue
            candles: List[Dict[str, Any]] = []
            prev_close: float | None = None
            for _, row in df.iterrows():
                close = float(row["Close"])
                change = (
                    float(row["Change"])
                    if "Change" in df.columns and pd.notna(row.get("Change"))
                    else (close - prev_close if prev_close is not None else None)
                )
                change_pct = (
                    float(row["ChangePercent"])
                    if "ChangePercent" in df.columns and pd.notna(row.get("ChangePercent"))
                    else ((change / prev_close) * 100 if prev_close not in (None, 0) and change is not None else None)
                )
                candles.append(
                    {
                        "date": row["Date"].strftime("%Y-%m-%d"),
                        "open": float(row["Open"]),
                        "high": float(row["High"]),
                        "low": float(row["Low"]),
                        "close": close,
                        "volume": float(row["Volume"])
                        if "Volume" in df.columns and pd.notna(row.get("Volume"))
                        else None,
                        "amount": float(row["Amount"])
                        if "Amount" in df.columns and pd.notna(row.get("Amount"))
                        else None,
                        "change": change,
                        "change_percent": change_pct,
                        "turnover_rate": float(row["TurnoverRate"])
                        if "TurnoverRate" in df.columns and pd.notna(row.get("TurnoverRate"))
                        else None,
                    }
                )
                prev_close = close
            return candles
        except Exception as exc:
            last_exc = exc
            continue

    if last_exc:
        logger.info(f"[kline] index fetch failed for {symbol}: {type(last_exc).__name__}: {last_exc}")
    return []
