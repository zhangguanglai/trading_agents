"""Data collector for TradingAgents with caching and parallel fetching."""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger(__name__)


class DataCollector:
    """Collects and caches stock data for analysis."""
    
    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._ref_count: Dict[str, int] = {}
    
    def _make_key(self, symbol: str, date: str, data_type: str) -> str:
        """Create a cache key for the given parameters."""
        return f"{symbol}:{date}:{data_type}"
    
    def ref(self, symbol: str, date: str) -> None:
        """Increment reference count for the given symbol/date."""
        key = f"{symbol}:{date}"
        self._ref_count[key] = self._ref_count.get(key, 0) + 1
    
    def evict(self, symbol: str, date: str) -> None:
        """Decrement reference count and evict if no longer needed."""
        key = f"{symbol}:{date}"
        if key in self._ref_count:
            self._ref_count[key] -= 1
            if self._ref_count[key] <= 0:
                # Remove all cached entries for this symbol/date
                keys_to_remove = [k for k in self._cache.keys() if k.startswith(key)]
                for k in keys_to_remove:
                    del self._cache[k]
                del self._ref_count[key]
    
    def get(self, symbol: str, date: str, data_type: str) -> Optional[Any]:
        """Get cached data if available."""
        key = self._make_key(symbol, date, data_type)
        return self._cache.get(key)
    
    def set(self, symbol: str, date: str, data_type: str, data: Any) -> None:
        """Cache the given data."""
        key = self._make_key(symbol, date, data_type)
        self._cache[key] = data
    
    def collect(self, symbol: str, date: str, horizons: Optional[List[str]] = None) -> Dict[str, Any]:
        """Collect all necessary data for analysis.
        
        Args:
            symbol: Stock symbol
            date: Trade date
            horizons: List of time horizons (e.g., ['short', 'medium'])
            
        Returns:
            Dictionary containing all collected data
        """
        from tradingagents.dataflows.interface import route_to_vendor
        
        horizons = horizons or ["short"]
        result = {"symbol": symbol, "date": date, "horizons": horizons}
        
        # Define data tasks
        tasks = []
        
        # Stock price data
        if not self.get(symbol, date, "stock_data"):
            tasks.append(("stock_data", lambda: route_to_vendor("get_stock_data", symbol)))
        
        # Fundamentals
        if not self.get(symbol, date, "fundamentals"):
            tasks.append(("fundamentals", lambda: route_to_vendor("get_fundamentals", symbol)))
        
        # News
        if not self.get(symbol, date, "news"):
            tasks.append(("news", lambda: route_to_vendor("get_news", symbol)))
        
        # Sentiment
        if not self.get(symbol, date, "sentiment"):
            tasks.append(("sentiment", lambda: route_to_vendor("get_social_sentiment", symbol)))
        
        # Execute tasks in parallel with optimized thread pool
        if tasks:
            fetch_start = time.time()
            # 优化：增加并发池大小以加速数据获取（生产环境使用更大池）
            with ThreadPoolExecutor(max_workers=min(16, len(tasks))) as executor:
                futures = {executor.submit(fn): name for name, fn in tasks}
                
                for future in futures:
                    name = futures[future]
                    try:
                        data = future.result(timeout=30)
                        self.set(symbol, date, name, data)
                        result[name] = data
                    except Exception as e:
                        logger.warning(f"Failed to fetch {name} for {symbol}: {e}")
                        result[name] = None
            
            logger.info(f"Data collection for {symbol} completed in {time.time() - fetch_start:.2f}s")
        
        # Add cached data to result
        for name, _ in tasks:
            if name not in result:
                result[name] = self.get(symbol, date, name)
        
        return result
