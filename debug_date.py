"""调试日期获取问题"""

import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

from tradingagents.dataflows.providers.cn_tushare_provider import CnTushareProvider
from datetime import datetime, timedelta

provider = CnTushareProvider()

# 获取最近7天的数据
end_date = datetime.now().strftime("%Y-%m-%d")
start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

print(f"查询日期范围: {start_date} ~ {end_date}")
print()

result = provider.get_stock_data('000960.SZ', start_date, end_date)
print(f"结果类型: {type(result)}")
print(f"结果内容: {result}")
