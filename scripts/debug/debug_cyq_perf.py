"""检查 cyq_perf 返回的数据"""

import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

from tradingagents.dataflows.providers.cn_tushare_provider import CnTushareProvider
from datetime import datetime, timedelta

provider = CnTushareProvider()

end_date = datetime.now().strftime("%Y-%m-%d")
start_date = (datetime.now() - timedelta(days=250)).strftime("%Y-%m-%d")

print(f"查询日期范围: {start_date} ~ {end_date}")
print()

result = provider.get_cyq_perf('000960.SZ', start_date, end_date)

if result is not None:
    import pandas as pd
    df = result.sort_values('trade_date')
    print(f"数据条数: {len(df)}")
    print(f"日期范围: {df['trade_date'].min()} ~ {df['trade_date'].max()}")
    print(f"\n最后5条数据:")
    print(df.tail()[['trade_date', 'weight_avg', 'winner_rate']])
    print(f"\n最新日期: {df['trade_date'].max()}")
    latest = df[df['trade_date'] == df['trade_date'].max()].iloc[0]
    print(f"最新数据:")
    print(f"  weight_avg: {latest.get('weight_avg')}")
    print(f"  winner_rate: {latest.get('winner_rate')}")
else:
    print("无数据")
