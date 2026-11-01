"""Chip-deep (筹码深度分析) router."""

from __future__ import annotations

import os
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.database import UserDB
from api.dependencies import _optional_user
from tradingagents.chip_deep import ChipDeepAnalyzer, ChipDeepResult

router = APIRouter(tags=["chip-deep"])

# 别名映射表（PRD v2.0 定义）
_CHIP_DEEP_ALIASES = {
    "茅台": "600519.SH", "贵州茅台": "600519.SH",
    "招行": "600036.SH", "招商银行": "600036.SH",
    "平安": "601318.SH", "中国平安": "601318.SH",
    "神华": "601088.SH", "中国神华": "601088.SH",
    "重汽": "000951.SZ", "中国重汽": "000951.SZ",
    "宁王": "300750.SZ", "宁德时代": "300750.SZ",
    "迪王": "002594.SZ", "比亚迪": "002594.SZ",
}

# stock_basic 缓存（名称 → ts_code），会话内缓存已解析的结果
_stock_name_cache: dict[str, str] = {}


def _resolve_stock_name_to_code(name_or_symbol: str) -> str:
    """将股票名称或代码解析为标准的 ts_code 格式。
    
    支持输入：
    - 纯数字代码：如 "000951" → "000951.SZ"
    - 标准代码：如 "000951.SZ" 或 "600519.SH"
    - 股票名称：如 "华工科技" → 查询 stock_basic 返回 "000988.SZ"
    - 别名：如 "茅台" → "600519.SH"
    """
    s = name_or_symbol.strip()
    
    # 1. 已是标准格式（含 .）
    if "." in s:
        return s.upper()
    
    # 2. 纯数字代码，自动补全后缀
    if s.isdigit() and len(s) in (6,):
        code = s.upper()
        if code.startswith("6"):
            return f"{code}.SH"
        elif code.startswith("0") or code.startswith("3"):
            return f"{code}.SZ"
        elif code.startswith("8") or code.startswith("4"):
            return f"{code}.BJ"
    
    # 3. 别名匹配
    alias_result = _CHIP_DEEP_ALIASES.get(s)
    if alias_result:
        return alias_result
    
    # 4. 名称查询：每次从Tushare拉取，避免缓存过期
    # 先查会话内已解析的缓存
    if s in _stock_name_cache:
        return _stock_name_cache[s]
    
    # 从Tushare拉取stock_basic
    try:
        import tushare as ts
        token = os.getenv("TUSHARE_TOKEN", "")
        if token:
            ts.set_token(token)
            api = ts.pro_api()
            df = api.stock_basic(fields="ts_code,name")
            if df is not None and not df.empty:
                # 精确匹配
                match = df[df["name"] == s]
                if not match.empty:
                    code = str(match.iloc[0]["ts_code"])
                    _stock_name_cache[s] = code
                    return code
                # 模糊匹配（包含）
                match = df[df["name"].str.contains(s, na=False)]
                if not match.empty:
                    code = str(match.iloc[0]["ts_code"])
                    _stock_name_cache[s] = code
                    return code
    except Exception as e:
        print(f"[chip-deep] 股票名称解析失败: {e}")
    
    # 5. 如果都不是，返回原始输入（让后续逻辑处理）
    return s.upper()


@router.get("/v1/chip-deep/analyze", response_model=ChipDeepResult)
async def chip_deep_analyze(
    symbol: str = Query(..., description="股票代码或名称，如 000951.SZ 或 中国重汽"),
    lookback_days: int = Query(30, ge=30, le=500, description="回溯天数"),
    current_user: Optional[UserDB] = Depends(_optional_user),
):
    """筹码深度分析主接口
    
    输入股票代码或名称，返回筹码分布、六维评分、综合评级等完整分析结果。
    """
    # 统一解析：支持代码、名称、别名
    symbol = _resolve_stock_name_to_code(symbol)
    print(f"[chip-deep] 解析后的代码: {symbol}")
    
    analyzer = ChipDeepAnalyzer(symbol, lookback_days)
    result = await analyzer.analyze()
    return result


@router.get("/v1/chip-deep/search")
async def chip_deep_search(
    q: str = Query(..., min_length=1, max_length=20, description="搜索关键词"),
):
    """智能搜索（支持别名、名称、代码模糊匹配）"""
    q = q.strip()
    results = []
    
    # 1. 别名匹配
    for alias, code in _CHIP_DEEP_ALIASES.items():
        if q in alias or alias in q:
            results.append({
                "ts_code": code,
                "name": alias,
                "match_type": "alias",
            })
    
    # 2. stock_basic 名称匹配（每次从Tushare拉取）
    try:
        import tushare as ts
        token = os.getenv("TUSHARE_TOKEN", "")
        if token:
            ts.set_token(token)
            api = ts.pro_api()
            df = api.stock_basic(fields="ts_code,name")
            if df is not None and not df.empty:
                # 名称包含关键词
                match = df[df["name"].str.contains(q, na=False)]
                for _, row in match.head(10).iterrows():
                    code = str(row["ts_code"])
                    name = str(row["name"])
                    # 避免与别名重复
                    if not any(r["ts_code"] == code for r in results):
                        results.append({
                            "ts_code": code,
                            "name": name,
                            "match_type": "name",
                        })
    except Exception as e:
        print(f"[chip-deep] 股票搜索失败: {e}")
    
    # 去重
    seen = set()
    unique = []
    for r in results:
        if r["ts_code"] not in seen:
            seen.add(r["ts_code"])
            unique.append(r)
    
    return {"code": 0, "data": unique}
