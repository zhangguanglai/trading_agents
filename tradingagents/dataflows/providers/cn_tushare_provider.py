import os
import json
from typing import Optional

from .base import BaseMarketDataProvider


class CnTushareProvider(BaseMarketDataProvider):
    """Tushare A-share data provider.

    Requires ``tushare`` package and ``TUSHARE_TOKEN`` environment variable.
    """

    def __init__(self):
        self._ts = None
        self._token = os.getenv("TUSHARE_TOKEN", "")

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

    @property
    def name(self) -> str:
        return "cn_tushare"

    def _normalize_symbol(self, symbol: str) -> str:
        """Convert 600519.SH -> 600519.SH (tushare format)."""
        if "." in symbol:
            return symbol
        # Assume 6-digit code
        if symbol.startswith("6"):
            return f"{symbol}.SH"
        return f"{symbol}.SZ"

    def _to_tushare_code(self, symbol: str) -> str:
        """Convert 600519.SH -> 600519.SH (tushare uses same format)."""
        return self._normalize_symbol(symbol)

    def get_stock_data(self, symbol: str, start_date: str, end_date: str) -> str:
        self._init_ts()
        ts_code = self._to_tushare_code(symbol)
        try:
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
