"""调试锡业股份 000960 数据问题"""

import asyncio
import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer

async def debug():
    analyzer = ChipDeepAnalyzer('000960.SZ')
    
    # 获取 perf_df
    perf_df = await analyzer._get_cyq_perf()
    if perf_df is not None:
        perf_df = perf_df.sort_values('trade_date').reset_index(drop=True)
        latest_date = perf_df['trade_date'].max()
        print(f'perf_df 最新日期: {latest_date}')
        print(f'perf_df 列名: {list(perf_df.columns)}')
        print(f'perf_df 最后几行:')
        print(perf_df.tail(3))
        
        # 获取收盘价
        close_price = await analyzer._get_close_price(latest_date)
        print(f'\n_get_close_price({latest_date}): {close_price}')
        
        # 获取筹码分布
        chips_df = await analyzer._get_cyq_chips(latest_date)
        if chips_df is not None:
            print(f'\nchips_df 日期: {latest_date}')
            print(f'chips_df 价格范围: {chips_df["price"].min():.2f} ~ {chips_df["price"].max():.2f}')
            
            # 计算筹码密度
            density, vacuum = analyzer._calc_chip_density_v2(chips_df, close_price)
            print(f'\n筹码密度: {density:.1f}%')
            print(f'真空悬崖: {vacuum}')

asyncio.run(debug())
