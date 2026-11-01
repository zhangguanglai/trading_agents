"""检查缓存问题"""

import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

from tradingagents.chip_deep.cache import get_cached

# 检查缓存的 cyq_perf 数据
cached = get_cached('000960.SZ', '2025-09-15_2026-06-02', 'cyq_perf')

if cached is not None:
    import pandas as pd
    df = cached
    print(f"缓存数据条数: {len(df)}")
    print(f"缓存数据日期范围: {df['trade_date'].min()} ~ {df['trade_date'].max()}")
    print(f"\n最后几条数据:")
    print(df.tail(3)[['trade_date', 'weight_avg', 'winner_rate']])
else:
    print("无缓存数据")
