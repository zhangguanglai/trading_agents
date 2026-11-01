"""调试601899的否决项触发原因"""

import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

import asyncio
from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer

async def test():
    analyzer = ChipDeepAnalyzer("601899.SH")
    result = await analyzer.analyze()
    
    print("=" * 70)
    print("紫金矿业 601899 否决项调试")
    print("=" * 70)
    print()
    
    dim6 = result.dim6_score
    total = result.dim6_total
    
    print("【六维评分详情】")
    print(f"  ①筹码密度: score={dim6.chip_density.score}, label={dim6.chip_density.label}, detail={dim6.chip_density.detail}")
    print(f"  ②边际变化: score={dim6.margin_change.score}, label={dim6.margin_change.label}, detail={dim6.margin_change.detail}")
    print(f"  ③获利盘: score={dim6.winner_position.score}, label={dim6.winner_position.label}, detail={dim6.winner_position.detail}")
    print(f"  ④成本抬升: score={dim6.cost_rise.score}, label={dim6.cost_rise.label}, detail={dim6.cost_rise.detail}")
    print(f"  ⑤超跌程度: score={dim6.overshoot.score}, label={dim6.overshoot.label}, detail={dim6.overshoot.detail}")
    print(f"  ⑥下方支撑: score={dim6.support_level.score}, label={dim6.support_level.label}, detail={dim6.support_level.detail}")
    print(f"  总分: {total:.2f}")
    print()
    
    print("【否决项逐项检查】")
    print("-" * 70)
    
    # 1. 筹码密度
    print(f"1. 筹码密度 label == '❌': {dim6.chip_density.label == '❌'}")
    
    # 2. 边际变化 + 恐慌出逃
    print(f"2. 边际变化 label == '❌': {dim6.margin_change.label == '❌'}")
    print(f"   '恐慌出逃' in detail: {'恐慌出逃' in dim6.margin_change.detail}")
    
    # 3. 成本抬升
    print(f"3. 成本抬升 label == '❌': {dim6.cost_rise.label == '❌'}")
    
    # 4. 获利盘 + 劣质低胜率
    print(f"4. 获利盘 label == '❌': {dim6.winner_position.label == '❌'}")
    print(f"   '劣质低胜率' in detail: {'劣质低胜率' in dim6.winner_position.detail}")
    print()
    
    # 手动检查恐慌出逃（使用实际数据）
    print("【恐慌出逃实际计算】")
    print("-" * 70)
    
    # 获取chips数据
    from tradingagents.chip_deep.cache import get_cached
    import pandas as pd
    
    # 从缓存获取数据
    chips_df = get_cached("601899.SH", "20260602", "cyq_chips")
    prev_chips_df = get_cached("601899.SH", "20260519", "cyq_chips")  # 2周前
    
    if chips_df is not None and prev_chips_df is not None:
        close = result.current.get('close', 0)
        
        above_curr = chips_df[chips_df["price"] > close]["percent"].sum()
        above_prev = prev_chips_df[prev_chips_df["price"] > close]["percent"].sum()
        above_change = above_curr - above_prev
        
        below_curr = chips_df[chips_df["price"] <= close]["percent"].sum()
        below_prev = prev_chips_df[prev_chips_df["price"] <= close]["percent"].sum()
        below_change = below_curr - below_prev
        
        print(f"  当前价: {close}")
        print(f"  上方: 当前 {above_curr:.1f}% → 2周前 {above_prev:.1f}% (变化 {above_change:+.1f}%)")
        print(f"  下方: 当前 {below_curr:.1f}% → 2周前 {below_prev:.1f}% (变化 {below_change:+.1f}%)")
        print(f"  恐慌出逃判定: 上方变化 < -15% 且 下方变化 > 10%")
        print(f"  结果: {above_change < -15 and below_change > 10}")
    else:
        print("  缓存数据不可用")

asyncio.run(test())
