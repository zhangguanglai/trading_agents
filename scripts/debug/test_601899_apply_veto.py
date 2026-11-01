"""直接调试 _apply_veto_rules"""

import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

import asyncio
from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer

async def test():
    analyzer = ChipDeepAnalyzer("601899.SH")
    result = await analyzer.analyze()
    
    print("=" * 70)
    print("直接调试 _apply_veto_rules")
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
    
    from tradingagents.chip_deep.cache import get_cached
    chips_df = get_cached("601899.SH", "20260602", "cyq_chips")
    prev_chips_df = get_cached("601899.SH", "20260519", "cyq_chips")
    close = result.current.get('close', 0)
    
    print(f"chips_df is None: {chips_df is None}")
    print(f"prev_chips_df is None: {prev_chips_df is None}")
    print(f"close: {close}")
    print()
    
    if chips_df is not None:
        print(f"chips_df.empty: {chips_df.empty}")
        print(f"chips_df.shape: {chips_df.shape}")
    
    if prev_chips_df is not None:
        print(f"prev_chips_df.empty: {prev_chips_df.empty}")
        print(f"prev_chips_df.shape: {prev_chips_df.shape}")
    
    print()
    
    # 直接调用 _apply_veto_rules
    max_rating = analyzer._apply_veto_rules(dim6, chips_df, prev_chips_df, close)
    print(f"_apply_veto_rules 返回: {max_rating}")
    
    # 手动检查每一步
    print()
    print("【手动检查】")
    print("-" * 70)
    
    # 检查1
    if dim6["chip_density"]["label"] == "❌":
        print("1. chip_density == '❌' → return 2")
    else:
        print("1. chip_density != '❌' → 继续")
    
    # 检查2
    if chips_df is not None and prev_chips_df is not None and close > 0:
        panic = analyzer._check_panic_exit(chips_df, prev_chips_df, close)
        print(f"2. _check_panic_exit({panic}) → {'return 2' if panic else '继续'}")
    else:
        print("2. 数据不可用 → 检查 label 条件")
        if dim6["margin_change"]["label"] == "❌" and "恐慌出逃" in dim6["margin_change"]["detail"]:
            print("   label == '❌' and '恐慌出逃' in detail → return 2")
        else:
            print("   不满足 → 继续")
    
    # 检查3
    if dim6["cost_rise"]["label"] == "❌":
        print("3. cost_rise == '❌' → return 2")
    else:
        print("3. cost_rise != '❌' → 继续")
    
    # 检查4
    if dim6["winner_position"]["label"] == "❌" and "劣质低胜率" in dim6["winner_position"]["detail"]:
        print("4. winner_position == '❌' and '劣质低胜率' → return 2")
    else:
        print("4. 不满足 → return 5")

asyncio.run(test())
