"""实际调试601899的否决项"""

import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

import asyncio
from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer

async def test():
    analyzer = ChipDeepAnalyzer("601899.SH")
    result = await analyzer.analyze()
    
    print("=" * 70)
    print("紫金矿业 601899 否决项实际调试")
    print("=" * 70)
    print()
    
    dim6 = {
        "chip_density": {"score": result.dim6_score.chip_density.score, "label": result.dim6_score.chip_density.label, "detail": result.dim6_score.chip_density.detail},
        "margin_change": {"score": result.dim6_score.margin_change.score, "label": result.dim6_score.margin_change.label, "detail": result.dim6_score.margin_change.detail},
        "winner_position": {"score": result.dim6_score.winner_position.score, "label": result.dim6_score.winner_position.label, "detail": result.dim6_score.winner_position.detail},
        "cost_rise": {"score": result.dim6_score.cost_rise.score, "label": result.dim6_score.cost_rise.label, "detail": result.dim6_score.cost_rise.detail},
        "overshoot": {"score": result.dim6_score.overshoot.score, "label": result.dim6_score.overshoot.label, "detail": result.dim6_score.overshoot.detail},
        "support_level": {"score": result.dim6_score.support_level.score, "label": result.dim6_score.support_level.label, "detail": result.dim6_score.support_level.detail},
    }
    
    print("【dim6 数据】")
    for key, val in dim6.items():
        print(f"  {key}: {val}")
    print()
    
    print("【否决项检查】")
    print("-" * 70)
    
    # 1. 筹码密度
    check1 = dim6["chip_density"]["label"] == "❌"
    print(f"1. chip_density label == '❌': {check1}")
    if check1:
        print("   → 触发否决！")
    
    # 2. 恐慌出逃（实际计算）
    from tradingagents.chip_deep.cache import get_cached
    chips_df = get_cached("601899.SH", "20260602", "cyq_chips")
    prev_chips_df = get_cached("601899.SH", "20260519", "cyq_chips")
    close = result.current.get('close', 0)
    
    if chips_df is not None and prev_chips_df is not None and close > 0:
        check2 = analyzer._check_panic_exit(chips_df, prev_chips_df, close)
        print(f"2. _check_panic_exit: {check2}")
        if check2:
            print("   → 触发否决！")
    else:
        check2 = False
        print(f"2. _check_panic_exit: 数据不可用")
    
    # 3. 成本抬升
    check3 = dim6["cost_rise"]["label"] == "❌"
    print(f"3. cost_rise label == '❌': {check3}")
    if check3:
        print("   → 触发否决！")
    
    # 4. 劣质低胜率
    check4 = dim6["winner_position"]["label"] == "❌" and "劣质低胜率" in dim6["winner_position"]["detail"]
    print(f"4. winner_position label == '❌' and '劣质低胜率' in detail: {check4}")
    if check4:
        print("   → 触发否决！")
    
    print()
    
    # 手动计算 _apply_veto_rules
    print("【手动计算 _apply_veto_rules】")
    print("-" * 70)
    if check1:
        max_rating = 2
        print("  筹码密度 ❌ → max_rating = 2")
    elif check2:
        max_rating = 2
        print("  恐慌出逃 → max_rating = 2")
    elif check3:
        max_rating = 2
        print("  成本抬升 ❌ → max_rating = 2")
    elif check4:
        max_rating = 2
        print("  劣质低胜率 → max_rating = 2")
    else:
        max_rating = 5
        print("  无否决项 → max_rating = 5")
    
    print()
    
    # 计算基础评级
    total = result.dim6_total
    base_rating = analyzer._calc_base_rating(total)
    final_rating = min(base_rating, max_rating)
    
    print("【评级计算】")
    print("-" * 70)
    print(f"  total = {total:.2f}")
    print(f"  base_rating = {base_rating}")
    print(f"  max_rating = {max_rating}")
    print(f"  final_rating = min({base_rating}, {max_rating}) = {final_rating}")
    print()
    
    print("【对比】")
    print("-" * 70)
    print(f"  实际返回的 rating: {result.rating}")
    print(f"  手动计算的 rating: {final_rating}")
    print(f"  一致: {'✅' if result.rating == final_rating else '❌'}")

asyncio.run(test())
