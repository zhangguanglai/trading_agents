import os
import json
import time
from typing import Optional

import pandas as pd

from .base import BaseMarketDataProvider


class CnTushareProvider(BaseMarketDataProvider):
    """Tushare A-share and Hong Kong stock data provider.

    Requires ``tushare`` package and ``TUSHARE_TOKEN`` environment variable.
    """

    def __init__(self):
        self._ts = None
        self._token = os.getenv("TUSHARE_TOKEN", "")
        self._max_retries = int(os.getenv("TA_TUSHARE_RETRIES", "2"))
        self._retry_delay = float(os.getenv("TA_TUSHARE_RETRY_DELAY", "1.0"))

    def _init_ts(self):
        if self._ts is not None:
            return
        try:
            import tushare as ts
            if not self._token:
                raise RuntimeError("TUSHARE_TOKEN environment variable is not set")
            ts.set_token(self._token)
            self._ts = ts.pro_api()
        except ImportError:
            raise RuntimeError(
                "cn_tushare requires 'tushare'. Install it with: pip install tushare"
            )

    def _call_with_retry(self, func, *args, **kwargs) -> pd.DataFrame | None:
        """Call Tushare API with retry logic for transient connection errors."""
        last_exc = None
        for attempt in range(self._max_retries + 1):
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as exc:
                last_exc = exc
                # Only retry on transient network errors
                if any(kw in str(exc).lower() for kw in ["connection", "timeout", "reset", "network", "socket"]):
                    if attempt < self._max_retries:
                        delay = self._retry_delay * (2 ** attempt)
                        print(f"[Tushare] Retry {attempt + 1}/{self._max_retries} after {delay:.1f}s ({type(exc).__name__})")
                        time.sleep(delay)
                        continue
                break
        raise last_exc

    @property
    def name(self) -> str:
        return "cn_tushare"

    def _is_hk_stock(self, symbol: str) -> bool:
        """Check if symbol is a Hong Kong stock (e.g. 00700.HK)."""
        return symbol.strip().upper().endswith(".HK")

    def _normalize_symbol(self, symbol: str) -> str:
        """Convert to Tushare format.

        A-share: 600519 -> 600519.SH
        HK: 0700.HK -> 00700.HK (pad to 5 digits)
        """
        s = symbol.strip().upper()
        if s.endswith(".HK"):
            code = s[:-3]
            # Tushare HK codes are 5 digits, pad with leading zeros
            return f"{int(code):05d}.HK"
        if "." in s:
            return s
        # Assume 6-digit A-share code
        if s.startswith("6"):
            return f"{s}.SH"
        return f"{s}.SZ"

    def _to_tushare_code(self, symbol: str) -> str:
        """Convert to Tushare ts_code format."""
        return self._normalize_symbol(symbol)

    def get_stock_data(self, symbol: str, start_date: str, end_date: str) -> str:
        self._init_ts()
        ts_code = self._to_tushare_code(symbol)
        try:
            if self._is_hk_stock(symbol):
                # Use hk_daily for Hong Kong stocks
                df = self._ts.hk_daily(
                    ts_code=ts_code,
                    start_date=start_date.replace("-", ""),
                    end_date=end_date.replace("-", ""),
                )
            else:
                df = self._ts.daily(
                    ts_code=ts_code,
                    start_date=start_date.replace("-", ""),
                    end_date=end_date.replace("-", ""),
                )
            if df is None or df.empty:
                return json.dumps({"error": f"No data for {symbol}"})
            # Rename columns to standard format
            df = df.rename(columns={
                "trade_date": "date",
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "vol": "volume",
                "amount": "amount",
            })
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            return df.to_json(orient="records", date_format="iso")
        except Exception as e:
            return json.dumps({"error": f"cn_tushare failed: {str(e)}"})

    def get_indicators(
        self, symbol: str, indicator: str, curr_date: str, look_back_days: int
    ) -> str:
        self._init_ts()
        # Tushare does not have a direct indicator API, return stock data for calculation
        start = pd.to_datetime(curr_date) - pd.Timedelta(days=look_back_days * 2)
        return self.get_stock_data(
            symbol, start.strftime("%Y-%m-%d"), curr_date
        )

    def get_fundamentals(self, ticker: str, curr_date: str = None) -> str:
        self._init_ts()
        if self._is_hk_stock(ticker):
            # HK fundamentals not available via standard daily_basic
            return json.dumps({"info": "cn_tushare HK fundamentals not available"})
        ts_code = self._to_tushare_code(ticker)
        try:
            df = self._ts.daily_basic(ts_code=ts_code, trade_date=curr_date.replace("-", "") if curr_date else None)
            if df is None or df.empty:
                return json.dumps({"error": f"No fundamentals for {ticker}"})
            return df.to_json(orient="records", date_format="iso")
        except Exception as e:
            return json.dumps({"error": f"cn_tushare fundamentals failed: {str(e)}"})

    def get_balance_sheet(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> str:
        self._init_ts()
        if self._is_hk_stock(ticker):
            return json.dumps({"info": "cn_tushare HK balance sheet not available"})
        ts_code = self._to_tushare_code(ticker)
        try:
            df = self._ts.balancesheet(ts_code=ts_code)
            if df is None or df.empty:
                return json.dumps({"error": f"No balance sheet for {ticker}"})
            return df.head(4).to_json(orient="records", date_format="iso")
        except Exception as e:
            return json.dumps({"error": f"cn_tushare balance sheet failed: {str(e)}"})

    def get_cashflow(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> str:
        self._init_ts()
        if self._is_hk_stock(ticker):
            return json.dumps({"info": "cn_tushare HK cashflow not available"})
        ts_code = self._to_tushare_code(ticker)
        try:
            df = self._ts.cashflow(ts_code=ts_code)
            if df is None or df.empty:
                return json.dumps({"error": f"No cashflow for {ticker}"})
            return df.head(4).to_json(orient="records", date_format="iso")
        except Exception as e:
            return json.dumps({"error": f"cn_tushare cashflow failed: {str(e)}"})

    def get_income_statement(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> str:
        self._init_ts()
        if self._is_hk_stock(ticker):
            return json.dumps({"info": "cn_tushare HK income statement not available"})
        ts_code = self._to_tushare_code(ticker)
        try:
            df = self._ts.income(ts_code=ts_code)
            if df is None or df.empty:
                return json.dumps({"error": f"No income statement for {ticker}"})
            return df.head(4).to_json(orient="records", date_format="iso")
        except Exception as e:
            return json.dumps({"error": f"cn_tushare income failed: {str(e)}"})

    def get_news(self, ticker: str, start_date: str, end_date: str) -> str:
        self._init_ts()
        # Tushare news API requires higher permissions
        return json.dumps({"info": "cn_tushare news requires higher permission level"})

    def get_global_news(
        self, curr_date: str, look_back_days: int = 7, limit: int = 50
    ) -> str:
        self._init_ts()
        return json.dumps({"info": "cn_tushare global news not implemented"})

    def get_insider_transactions(self, symbol: str) -> str:
        self._init_ts()
        if self._is_hk_stock(symbol):
            return json.dumps({"info": "cn_tushare HK insider transactions not available"})
        ts_code = self._to_tushare_code(symbol)
        try:
            df = self._ts.stk_holdertrade(ts_code=ts_code)
            if df is None or df.empty:
                return json.dumps({"error": f"No insider transactions for {symbol}"})
            return df.to_json(orient="records", date_format="iso")
        except Exception as e:
            return json.dumps({"error": f"cn_tushare insider transactions failed: {str(e)}"})

    def get_realtime_quotes(self, symbols: list[str]) -> str:
        self._init_ts()
        result = {}
        for symbol in symbols:
            ts_code = self._to_tushare_code(symbol)
            try:
                if self._is_hk_stock(symbol):
                    df = self._ts.hk_daily(ts_code=ts_code, limit=1)
                else:
                    df = self._ts.daily(ts_code=ts_code, limit=1)
                if df is not None and not df.empty:
                    row = df.iloc[0]
                    result[symbol] = {
                        "price": float(row["close"]),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "previous_close": float(row["pre_close"]) if "pre_close" in row else float(row["close"]),
                        "change": float(row["change"]) if "change" in row else 0.0,
                        "change_pct": float(row["pct_chg"]) if "pct_chg" in row else 0.0,
                        "volume": int(row["vol"]) if "vol" in row else 0,
                        "amount": float(row["amount"]) if "amount" in row else 0.0,
                    }
            except Exception:
                pass
        return json.dumps(result)

    def get_individual_fund_flow(self, symbol: str) -> str:
        """获取个股近期主力资金净流向（港股暂不支持）。"""
        if self._is_hk_stock(symbol):
            return f"{symbol} 为港股标的，Tushare 暂不提供港股资金流向数据。"
        self._init_ts()
        ts_code = self._to_tushare_code(symbol)
        try:
            df = self._call_with_retry(self._ts.moneyflow, ts_code=ts_code)
            if df is None or df.empty:
                return f"{symbol} 近期主力资金流向数据暂不可用。"
            df_recent = df.tail(5)
            return f"{symbol} 近5日主力资金净流向：\n{df_recent.to_string(index=False)}"
        except Exception as e:
            return None

    def get_lhb_detail(self, symbol: str, date: str) -> str:
        """获取龙虎榜数据（港股暂不支持）。"""
        if self._is_hk_stock(symbol):
            return f"{symbol} 为港股标的，港股无龙虎榜机制。"
        self._init_ts()
        ts_code = self._to_tushare_code(symbol)
        try:
            df = self._call_with_retry(self._ts.top_list, ts_code=ts_code, trade_date=date.replace("-", ""))
            if df is None or df.empty:
                return f"{symbol} 在 {date} 无龙虎榜数据（非异动日属正常）。"
            return f"{symbol} 龙虎榜明细（{date}）：\n{df.head(20).to_string(index=False)}"
        except Exception as e:
            return None

    def get_board_fund_flow(self) -> str:
        """获取行业板块资金流向排名。"""
        self._init_ts()
        try:
            df = self._call_with_retry(self._ts.moneyflow_industry)
            if df is None or df.empty:
                return "今日板块资金流向数据暂不可用。"
            return f"行业板块资金流向排名：\n{df.head(20).to_string(index=False)}"
        except Exception as e:
            return None

    def get_zt_pool(self, date: str) -> str:
        """获取涨停板情绪池。"""
        self._init_ts()
        try:
            df = self._call_with_retry(self._ts.limit_list, trade_date=date.replace("-", ""))
            if df is None or df.empty:
                return f"{date} 涨停板数据暂不可用。"
            return f"涨停板情绪池（{date}）：\n{df.head(30).to_string(index=False)}"
        except Exception as e:
            return None

    def get_cyq_perf(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame | None:
        """获取筹码性能指标 (cyq_perf)。"""
        self._init_ts()
        ts_code = self._to_tushare_code(symbol)
        try:
            df = self._call_with_retry(
                self._ts.cyq_perf,
                ts_code=ts_code,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
            )
            return df if df is not None and not df.empty else None
        except Exception:
            return None

    def get_cyq_chips(self, symbol: str, trade_date: str) -> pd.DataFrame | None:
        """获取筹码分布明细 (cyq_chips)。"""
        self._init_ts()
        ts_code = self._to_tushare_code(symbol)
        try:
            df = self._call_with_retry(
                self._ts.cyq_chips,
                ts_code=ts_code,
                trade_date=trade_date.replace("-", ""),
            )
            return df if df is not None and not df.empty else None
        except Exception:
            return None

    def get_hot_stocks_xq(self) -> str:
        """获取雪球热搜股票列表（Tushare 暂不支持，返回提示）。"""
        return "雪球热搜数据暂不可用（Tushare 未提供该接口）。"
