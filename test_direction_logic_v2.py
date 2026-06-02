"""验证边际变化方向判断逻辑 - 使用实际筹码变化数据"""

import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

import pandas as pd
from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer

analyzer = ChipDeepAnalyzer("601899.SH")

print("=" * 70)
print("边际变化方向判断逻辑验证 v2")
print("=" * 70)
print()

# 构造测试数据：模拟不同场景的筹码变化
# 场景1：紫金矿业型（当前价在分箱内偏上，筹码增加）
chips_df_1 = pd.DataFrame({
    "price": [28.0, 29.0, 30.0, 31.0, 32.0, 33.0, 34.0, 35.0],
    "percent": [2.0, 3.0, 5.0, 8.0, 6.0, 4.0, 3.0, 2.0]  # [30,32) 增加
})

prev_chips_df_1 = pd.DataFrame({
    "price": [28.0, 29.0, 30.0, 31.0, 32.0, 33.0, 34.0, 35.0],
    "percent": [2.0, 3.0, 5.0, 5.0, 4.0, 4.0, 3.0, 2.0]  # [30,32) 从 5% 增加到 8%
})

# 场景2：当前价在分箱内偏下（如 30.5 在 [30,32) 内）
chips_df_2 = pd.DataFrame({
    "price": [28.0, 29.0, 30.0, 31.0, 32.0, 33.0, 34.0, 35.0],
    "percent": [2.0, 3.0, 5.0, 8.0, 6.0, 4.0, 3.0, 2.0]
})

prev_chips_df_2 = pd.DataFrame({
    "price": [28.0, 29.0, 30.0, 31.0, 32.0, 33.0, 34.0, 35.0],
    "percent": [2.0, 3.0, 5.0, 5.0, 4.0, 4.0, 3.0, 2.0]
})

# 场景3：当前价在分箱上方（如 33 在 [32,34) 上方）
chips_df_3 = pd.DataFrame({
    "price": [28.0, 29.0, 30.0, 31.0, 32.0, 33.0, 34.0, 35.0],
    "percent": [2.0, 3.0, 5.0, 4.0, 8.0, 6.0, 3.0, 2.0]  # [32,34) 增加
})

prev_chips_df_3 = pd.DataFrame({
    "price": [28.0, 29.0, 30.0, 31.0, 32.0, 33.0, 34.0, 35.0],
    "percent": [2.0, 3.0, 5.0, 4.0, 5.0, 4.0, 3.0, 2.0]  # [32,34) 从 5% 增加到 8%
})

# 场景4：低价股（当前价 15 在 [14,16) 内）
chips_df_4 = pd.DataFrame({
    "price": [12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0],
    "percent": [2.0, 3.0, 5.0, 8.0, 6.0, 4.0, 3.0, 2.0]
})

prev_chips_df_4 = pd.DataFrame({
    "price": [12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0],
    "percent": [2.0, 3.0, 5.0, 5.0, 4.0, 4.0, 3.0, 2.0]
})

test_cases = [
    (31.58, chips_df_1, prev_chips_df_1, "向上集中", "紫金矿业：当前价31.58在[30,32)内偏上"),
    (30.50, chips_df_2, prev_chips_df_2, "向上集中", "当前价30.5在[30,32)内偏下"),
    (33.00, chips_df_3, prev_chips_df_3, "向上集中", "当前价33在[32,34)内"),
    (15.00, chips_df_4, prev_chips_df_4, "向上集中", "低价股：当前价15在[14,16)内"),
]

print("【方向判断测试】")
print("-" * 70)

all_pass = True
for close, chips, prev_chips, expected_direction, desc in test_cases:
    bin_low, bin_high = analyzer._get_current_bin(chips, close)
    margin, direction = analyzer._calc_margin_change_v2(chips, prev_chips, close)
    
    bin_center = (bin_low + bin_high) / 2
    threshold = close * 0.98
    
    print(f"{desc}")
    print(f"  当前价 {close:.2f} → 分箱 [{bin_low:.0f}, {bin_high:.0f}), 中心 {bin_center:.1f}")
    print(f"  阈值: {threshold:.2f} (当前价 * 0.98)")
    print(f"  判断: bin_center {bin_center:.1f} >= {threshold:.2f} ? {bin_center >= threshold}")
    print(f"  边际变化: {margin:+.1f}%")
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
