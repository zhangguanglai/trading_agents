"""实际验证601899的评级计算"""

import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

import asyncio
from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer

async def test():
    analyzer = ChipDeepAnalyzer("601899.SH")
    result = await analyzer.analyze()
    
    print("=" * 70)
    print("紫金矿业 601899 实际数据验证")
    print("=" * 70)
    print()
    
    print("【元数据】")
    print(f"  股票: {result.meta.get('name', '')} ({result.meta.get('symbol', '')})")
    print(f"  数据日期: {result.meta.get('data_date', '')}")
    print()
    
    print("【当前价格】")
    print(f"  收盘价: {result.current.get('close', 0):.2f}")
    print(f"  平均成本: {result.current.get('weight_avg', 0):.2f}")
    print(f"  获利盘: {result.current.get('winner_rate', 0):.1f}%")
    print()
    
    print("【六维评分】")
    dim6 = result.dim6_score
    total = result.dim6_total
    print(f"  ①筹码密度: {dim6.chip_density.score} {dim6.chip_density.label} | {dim6.chip_density.detail}")
    print(f"  ②边际变化: {dim6.margin_change.score} {dim6.margin_change.label} | {dim6.margin_change.detail}")
    print(f"  ③获利盘: {dim6.winner_position.score} {dim6.winner_position.label} | {dim6.winner_position.detail}")
    print(f"  ④成本抬升: {dim6.cost_rise.score} {dim6.cost_rise.label} | {dim6.cost_rise.detail}")
    print(f"  ⑤超跌程度: {dim6.overshoot.score} {dim6.overshoot.label} | {dim6.overshoot.detail}")
    print(f"  ⑥下方支撑: {dim6.support_level.score} {dim6.support_level.label} | {dim6.support_level.detail}")
    print(f"  总分: {total:.2f}/5.5")
    print()
    
    print("【评级】")
    print(f"  评级: {'⭐' * result.rating} ({result.rating}星)")
    print()
    
    print("【总结】")
    print(f"  {result.summary_text}")
    print()
    
    # 验证评级映射
    expected_rating = analyzer._calc_base_rating(total)
    print("【验证】")
    print(f"  预期评级（基于总分 {total:.2f}）: {expected_rating}星")
    print(f"  实际评级: {result.rating}星")
    print(f"  一致: {'✅' if expected_rating == result.rating else '❌'}")

asyncio.run(test())
