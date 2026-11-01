"""核实各数据源的最新日期"""

import asyncio
import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

from tradingagents.dataflows.providers.cn_tushare_provider import CnTushareProvider
from datetime import datetime, timedelta
import json

provider = CnTushareProvider()

print("=" * 70)
print("锡业股份 000960 数据源日期核实")
print("=" * 70)
print()

# 1. 检查 cyq_perf 最新日期
print("【1. cyq_perf 筹码性能指标】")
print("-" * 70)
end_date = datetime.now().strftime("%Y-%m-%d")
start_date = (datetime.now() - timedelta(days=250)).strftime("%Y-%m-%d")

result = provider.get_cyq_perf('000960.SZ', start_date, end_date)
if result is not None:
    import pandas as pd
    if isinstance(result, pd.DataFrame):
        df = result.sort_values('trade_date')
        latest_date = df['trade_date'].max()
        latest_row = df[df['trade_date'] == latest_date].iloc[0]
        print(f"最新日期: {latest_date}")
        print(f"该日期数据:")
        print(f"  close: {latest_row.get('close', 'N/A')}")
        print(f"  weight_avg: {latest_row.get('weight_avg', 'N/A')}")
        print(f"  winner_rate: {latest_row.get('winner_rate', 'N/A')}")
        print(f"  cost_5pct: {latest_row.get('cost_5pct', 'N/A')}")
        print(f"  cost_95pct: {latest_row.get('cost_95pct', 'N/A')}")
print()

# 2. 检查 cyq_chips 最新日期
print("【2. cyq_chips 筹码分布】")
print("-" * 70)
# 尝试获取 2026-06-02 的数据
for date in ['20260602', '20260601']:
    result = provider.get_cyq_chips('000960.SZ', date)
    if result is not None:
        print(f"日期 {date}: 有数据")
        if isinstance(result, pd.DataFrame):
            print(f"  价格范围: {result['price'].min():.2f} ~ {result['price'].max():.2f}")
            print(f"  数据条数: {len(result)}")
    else:
        print(f"日期 {date}: 无数据")
print()

# 3. 检查 daily 最新日期
print("【3. daily 日线数据】")
print("-" * 70)
start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
result = provider.get_stock_data('000960.SZ', start_date, end_date)
if result is not None:
    if isinstance(result, str):
        data = json.loads(result)
        if isinstance(data, list):
            print(f"数据条数: {len(data)}")
            print(f"最新日期: {data[0].get('date')}")
            print(f"最新数据:")
            print(f"  close: {data[0].get('close')}")
            print(f"  open: {data[0].get('open')}")
            print(f"  high: {data[0].get('high')}")
            print(f"  low: {data[0].get('low')}")
            print(f"\n数据日期列表:")
            for item in data:
                print(f"  {item.get('date')}: close={item.get('close')}")
print()

print("=" * 70)
print("核实完成！")
print("=" * 70)
