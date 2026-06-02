"""调试日期获取问题"""

import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

from tradingagents.dataflows.providers.cn_tushare_provider import CnTushareProvider
from datetime import datetime, timedelta
import json

provider = CnTushareProvider()

# 获取最近7天的数据
end_date = datetime.now().strftime("%Y-%m-%d")
start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

result = provider.get_stock_data('000960.SZ', start_date, end_date)
data = json.loads(result)

print(f"数据条数: {len(data)}")
print(f"第一条日期: {data[0].get('date')}")
print(f"最后一条日期: {data[-1].get('date')}")
print()

# 检查 _get_daily_latest_date 的逻辑
latest_date = data[-1].get("date", "")
print(f"latest_date: {latest_date}")
print(f"转换后: {latest_date.replace('-', '')}")
