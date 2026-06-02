"""验证边际变化方向判断逻辑"""

import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

import pandas as pd
from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer

analyzer = ChipDeepAnalyzer("601899.SH")

print("=" * 70)
print("边际变化方向判断逻辑验证")
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

# 测试场景
test_cases = [
    # (当前价, 分箱, 预期方向, 说明)
    (31.58, "[30,32)", "向上集中", "紫金矿业：分箱中心31.0 >= 31.58*0.98=30.95"),
    (30.5, "[30,32)", "向上集中", "分箱内偏低：分箱中心31.0 >= 30.5*0.98=29.89"),
    (32.5, "[32,34)", "向上集中", "分箱中心33.0 >= 32.5*0.98=31.85"),
    (29.0, "[28,30)", "向下承接", "分箱中心29.0 < 29.0*0.98=28.42 → 不满足"),
    (35.0, "[34,36)", "向上集中", "分箱中心35.0 >= 35.0*0.98=34.3"),
    (42.0, "[42,44)", "向上集中", "高价股：分箱中心43.0 >= 42.0*0.98=41.16"),
    (15.0, "[14,16)", "向上集中", "低价股：分箱中心15.0 >= 15.0*0.98=14.7"),
    (15.8, "[14,16)", "向上集中", "低价股偏上：分箱中心15.0 >= 15.8*0.98=15.48"),
]

print("【方向判断测试】")
print("-" * 70)

all_pass = True
for close, expected_bin, expected_direction, desc in test_cases:
    bin_low, bin_high = analyzer._get_current_bin(chips_df, close)
    margin, direction = analyzer._calc_margin_change_v2(chips_df, prev_chips_df, close)
    
    bin_center = (bin_low + bin_high) / 2
    threshold = close * 0.98
    
    print(f"当前价 {close:>5.2f} → 分箱 [{bin_low:.0f}, {bin_high:.0f}), 中心 {bin_center:.1f}")
    print(f"  阈值: {threshold:.2f} (当前价 * 0.98)")
    print(f"  判断: bin_center {bin_center:.1f} >= {threshold:.2f} ? {bin_center >= threshold}")
    print(f"  方向: {direction}")
    print(f"  预期: {expected_direction}")
    
    if direction == expected_direction:
        print(f"  结果: ✅")
    else:
        print(f"  结果: ❌")
        all_pass = False
    print()

print("=" * 70)
print("结论")
print("=" * 70)
print()
if all_pass:
    print("✅ 所有测试通过！方向判断逻辑正确")
else:
    print("❌ 存在测试失败，需要进一步调整")
print()

print("【逻辑说明】")
print("-" * 70)
print("核心原则：判断筹码增加的价格位置相对于当前价的方位")
print()
print("1. 分箱中心 >= 当前价 * 0.98")
print("   → 资金在当前价附近或上方买入（主动进攻）")
print("   → 方向：向上集中 ✅")
print()
print("2. 分箱中心 < 当前价 * 0.98")
print("   → 资金在低于当前价的位置买入（被动防守）")
print("   → 方向：向下承接 ⚠️")
print()
print("3. 阈值 0.98 的含义：")
print("   - 允许 2% 的误差范围，考虑分箱粒度")
print("   - 当前价 31.58，分箱 [30,32) 中心 31.0")
print("   - 31.0 >= 31.58 * 0.98 = 30.95 → 向上集中 ✅")
print()
