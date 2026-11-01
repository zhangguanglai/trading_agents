"""调试 _build_result 中的 rating 计算"""

import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

import asyncio
from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer

async def test():
    analyzer = ChipDeepAnalyzer("601899.SH")
    
    # 获取数据
    perf_df = await analyzer._get_cyq_perf()
    latest_date = perf_df.iloc[-1]["trade_date"]
    chips_df = await analyzer._get_cyq_chips(latest_date)
    prev_date = analyzer._get_prev_trade_date(perf_df, latest_date, days=14)
    prev_chips_df = await analyzer._get_cyq_chips(prev_date) if prev_date else None
    close_price = await analyzer._get_close_price(latest_date)
    
    print("=" * 70)
    print("调试 _build_result 中的 rating 计算")
    print("=" * 70)
    print()
    
    print(f"latest_date: {latest_date}")
    print(f"prev_date: {prev_date}")
    print(f"close_price: {close_price}")
    print(f"chips_df is None: {chips_df is None}")
    print(f"prev_chips_df is None: {prev_chips_df is None}")
    print()
    
    # 计算 dim6
    dim6 = analyzer._calc_dim6(perf_df, chips_df, prev_chips_df, close_price)
    
    print("【_calc_dim6 结果】")
    print(f"  total: {dim6['total']:.2f}")
    for key in ["chip_density", "margin_change", "winner_position", "cost_rise", "overshoot", "support_level"]:
        print(f"  {key}: score={dim6[key]['score']}, label={dim6[key]['label']}")
    print()
    
    # 手动计算 _build_result 中的 rating
    latest = perf_df.iloc[-1]
    peak_cost = analyzer._calc_peak_cost(chips_df) if chips_df is not None and not chips_df.empty else float(latest.get("weight_avg", 0))
    weight_avg = peak_cost
    close = close_price if close_price > 0 else weight_avg
    
    print(f"peak_cost: {peak_cost:.2f}")
    print(f"close: {close:.2f}")
    print()
    
    # 检查 chips_df 是否被修改
    print("【检查 chips_df 修改】")
    print(f"  原始 chips_df is None: {chips_df is None}")
    
    # 模拟 _build_result 中的修改
    if chips_df is not None and not chips_df.empty:
        chips_df_modified = chips_df.sort_values("price")
        print(f"  修改后 chips_df is None: {chips_df_modified is None}")
    
    # 调用 _apply_veto_rules（使用原始数据）
    max_rating_original = analyzer._apply_veto_rules(dim6, chips_df, prev_chips_df, close)
    print(f"  _apply_veto_rules(原始): {max_rating_original}")
    
    # 调用 _apply_veto_rules（使用修改后的数据）
    if chips_df is not None and not chips_df.empty:
        max_rating_modified = analyzer._apply_veto_rules(dim6, chips_df_modified, prev_chips_df, close)
        print(f"  _apply_veto_rules(修改后): {max_rating_modified}")
    
    print()
    
    # 计算 base_rating
    base_rating = analyzer._calc_base_rating(dim6["total"])
    print(f"base_rating: {base_rating}")
    
    # 计算 rating
    rating = min(base_rating, max_rating_original)
    print(f"rating (使用原始): {rating}")
    
    if chips_df is not None and not chips_df.empty:
        rating_modified = min(base_rating, max_rating_modified)
        print(f"rating (使用修改后): {rating_modified}")
    
    print()
    
    # 实际调用 analyze
    result = await analyzer.analyze()
    print(f"实际返回的 rating: {result.rating}")
    print(f"实际返回的 total: {result.dim6_total:.2f}")

asyncio.run(test())
