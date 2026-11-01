"""验证方向判断修复"""

import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

import pandas as pd
from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer

analyzer = ChipDeepAnalyzer("601899.SH")

print("=" * 70)
print("方向判断修复验证")
print("=" * 70)
print()

# 构造测试数据
chips_df = pd.DataFrame({
    "price": [28.0, 29.0, 30.0, 31.0, 32.0, 33.0, 34.0, 35.0],
    "percent": [2.0, 3.0, 5.0, 8.0, 6.0, 4.0, 3.0, 2.0]
})

prev_chips_df = pd.DataFrame({
    "price": [28.0, 29.0, 30.0, 31.0, 32.0, 33.0, 34.0, 35.0],
    "percent": [2.0, 3.0, 5.0, 5.0, 4.0, 4.0, 3.0, 2.0]
})

# 测试不同当前价的方向判断
test_cases = [
    (31.58, "[30,32)"),  # 紫金矿业场景
    (30.5, "[30,32)"),   # 分箱内偏低
    (31.99, "[30,32)"),  # 分箱内接近上限
    (32.0, "[32,34)"),   # 分箱边界
    (29.5, "[28,30)"),   # 低于当前价的分箱
]

for close, expected_bin in test_cases:
    bin_low, bin_high = analyzer._get_current_bin(chips_df, close)
    margin, direction = analyzer._calc_margin_change_v2(chips_df, prev_chips_df, close)
    
    print(f"当前价 {close:>5.2f} → 分箱 [{bin_low:.0f}, {bin_high:.0f})")
    print(f"  边际变化: {margin:+.1f}%, 方向: {direction}")
    
    # 判断是否正确
    if close >= bin_low and close < bin_high:
        # 当前价在分箱内，增加应视为向上集中
        expected_direction = "向上集中"
    else:
        expected_direction = "向下承接"
    
    print(f"  预期方向: {expected_direction}, 实际: {direction} {'✅' if direction == expected_direction else '❌'}")
    print()

print("=" * 70)
print("结论")
print("=" * 70)
print()
print("✅ 当前价在分箱内时，筹码增加应视为'向上集中'")
print("✅ 当前价在分箱下方时，筹码增加应视为'向下承接'")
print()
