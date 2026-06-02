"""调试601899的chips数据结构"""

import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

import asyncio
from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer
from tradingagents.chip_deep.cache import get_cached

async def test():
    analyzer = ChipDeepAnalyzer("601899.SH")
    
    # 获取chips数据
    chips_df = get_cached("601899.SH", "20260602", "cyq_chips")
    prev_chips_df = get_cached("601899.SH", "20260519", "cyq_chips")
    
    print("=" * 70)
    print("紫金矿业 601899 chips 数据结构调试")
    print("=" * 70)
    print()
    
    if chips_df is not None:
        print("【当前chips数据】")
        print(f"  形状: {chips_df.shape}")
        print(f"  列: {list(chips_df.columns)}")
        print(f"  前10行:")
        print(chips_df.head(10).to_string())
        print()
        
        print(f"  price范围: [{chips_df['price'].min():.2f}, {chips_df['price'].max():.2f}]")
        print(f"  percent总和: {chips_df['percent'].sum():.1f}%")
        print()
    
    if prev_chips_df is not None:
        print("【2周前chips数据】")
        print(f"  形状: {prev_chips_df.shape}")
        print(f"  前10行:")
        print(prev_chips_df.head(10).to_string())
        print()
    
    if chips_df is not None and prev_chips_df is not None:
        close = 31.58
        
        print("【恐慌出逃计算】")
        print("-" * 70)
        
        above_curr = chips_df[chips_df["price"] > close]["percent"].sum()
        above_prev = prev_chips_df[prev_chips_df["price"] > close]["percent"].sum()
        above_change = above_curr - above_prev
        
        below_curr = chips_df[chips_df["price"] <= close]["percent"].sum()
        below_prev = prev_chips_df[prev_chips_df["price"] <= close]["percent"].sum()
        below_change = below_curr - below_prev
        
        print(f"  当前价: {close}")
        print(f"  上方当前: {above_curr:.1f}%")
        print(f"  上方2周前: {above_prev:.1f}%")
        print(f"  上方变化: {above_change:+.1f}%")
        print()
        print(f"  下方当前: {below_curr:.1f}%")
        print(f"  下方2周前: {below_prev:.1f}%")
        print(f"  下方变化: {below_change:+.1f}%")
        print()
        print(f"  恐慌出逃: {above_change < -15 and below_change > 10}")
        print(f"    条件: 上方变化 < -15% → {above_change < -15}")
        print(f"    条件: 下方变化 > 10% → {below_change > 10}")

asyncio.run(test())
