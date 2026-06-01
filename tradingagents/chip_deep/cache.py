"""chip-deep 分级缓存模块"""

import hashlib
from pathlib import Path
from typing import Optional

import pandas as pd

CACHE_DIR = Path("./dataflows/data_cache/chip_deep")


def _get_cache_key(symbol: str, date: str, data_type: str) -> str:
    """生成缓存键"""
    return hashlib.md5(f"{symbol}:{date}:{data_type}".encode()).hexdigest()


def get_cached(symbol: str, date: str, data_type: str) -> Optional[pd.DataFrame]:
    """从缓存读取数据"""
    key = _get_cache_key(symbol, date, data_type)
    cache_file = CACHE_DIR / f"{key}.parquet"
    if cache_file.exists():
        try:
            return pd.read_parquet(cache_file)
        except Exception:
            return None
    return None


def set_cached(symbol: str, date: str, data_type: str, df: pd.DataFrame) -> None:
    """写入缓存"""
    try:
        key = _get_cache_key(symbol, date, data_type)
        cache_file = CACHE_DIR / f"{key}.parquet"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache_file)
    except Exception:
        pass  # 缓存失败不影响主流程
