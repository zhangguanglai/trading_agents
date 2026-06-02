"""验证边际变化分箱一致性"""

import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

import pandas as pd
import numpy as np
from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer
from tradingagents.chip_deep.models import MarginChangeItem

analyzer = ChipDeepAnalyzer("000960.SZ")

print("=" * 70)
print("边际变化分箱一致性验证")
print("=" * 70)
print()

# 构造测试数据（模拟000960的筹码分布）
# 中价股 step=2.0，分箱边界应为 [38, 40, 42, 44, 46, 48, 50]
chips_df = pd.DataFrame({
    "price": [38.0, 39.0, 40.0, 41.0, 42.0, 43.0, 44.0, 45.0, 46.0, 47.0, 48.0, 49.0, 50.0],
    "percent": [2.0, 3.0, 4.0, 5.0, 8.0, 7.2, 5.0, 4.0, 3.0, 2.5, 2.0, 1.5, 1.0]
})

prev_chips_df = pd.DataFrame({
    "price": [38.0, 39.0, 40.0, 41.0, 42.0, 43.0, 44.0, 45.0, 46.0, 47.0, 48.0, 49.0, 50.0],
    "percent": [2.0, 3.0, 4.0, 5.0, 5.0, 5.0, 8.0, 6.0, 3.0, 2.5, 2.0, 1.5, 1.0]
})

close = 43.19

print("【分箱边界验证】")
print("-" * 70)
step, _ = analyzer._get_bin_params(close)
bins = analyzer._get_price_bins(chips_df, close)
print(f"当前价: {close}")
print(f"分箱粒度: {step}")
print(f"分箱边界: {bins}")
print()

print("【当前价所在分箱】")
print("-" * 70)
bin_low, bin_high = analyzer._get_current_bin(chips_df, close)
print(f"当前价 {close} → 分箱 [{bin_low}, {bin_high})")
print()

print("【_calc_margin_change_v2 计算】")
print("-" * 70)
margin, direction = analyzer._calc_margin_change_v2(chips_df, prev_chips_df, close)
print(f"边际变化: {margin:+.1f}%")
print(f"方向: {direction}")
print()

print("【详细报告中的边际变化列表（新逻辑）】")
print("-" * 70)

# 模拟新的 _build_result 逻辑
def _aggregate_by_bins(df, bins):
    result = {}
    for i in range(len(bins) - 1):
        bl, bh = bins[i], bins[i + 1]
        mask = (df["price"] >= bl) & (df["price"] < bh)
        pct = df.loc[mask, "percent"].sum() if mask.any() else 0
        result[(float(bl), float(bh))] = pct
    return result

curr_by_bin = _aggregate_by_bins(chips_df, bins)
prev_by_bin = _aggregate_by_bins(prev_chips_df, bins)

margin_change = []
for (bl, bh), curr_pct in curr_by_bin.items():
    prev_pct = prev_by_bin.get((bl, bh), 0)
    change = curr_pct - prev_pct
    if abs(change) > 0.5:
        margin_change.append(MarginChangeItem(
            price_low=round(bl, 2),
            price_high=round(bh, 2),
            prev_pct=round(prev_pct, 2),
            curr_pct=round(curr_pct, 2),
            change=round(change, 2),
        ))

margin_change = sorted(margin_change, key=lambda x: abs(x.change), reverse=True)[:5]

for item in margin_change:
    direction = "↑" if item.change > 0 else "↓"
    print(f"  [{item.price_low:.1f}, {item.price_high:.1f}) {item.prev_pct:.1f}% → {item.curr_pct:.1f}% ({direction}{abs(item.change):.1f}%)")

print()

# 验证当前价所在分箱是否在列表中
print("【一致性检查】")
print("-" * 70)
current_bin_in_list = any(
    item.price_low == bin_low and item.price_high == bin_high 
    for item in margin_change
)
print(f"当前价分箱 [{bin_low}, {bin_high}) 在变化列表中: {'✅' if current_bin_in_list else '❌'}")

# 找到当前价分箱的变化
current_item = next(
    (item for item in margin_change if item.price_low == bin_low and item.price_high == bin_high),
    None
)
if current_item:
    print(f"当前价分箱变化: {current_item.change:+.1f}%")
    print(f"_calc_margin_change_v2 结果: {margin:+.1f}%")
    print(f"两者一致: {'✅' if abs(current_item.change - margin) < 0.01 else '❌'}")
else:
    print("❌ 当前价分箱不在变化列表中！")

print()
print("=" * 70)
print("结论")
print("=" * 70)
print()
if current_bin_in_list and current_item and abs(current_item.change - margin) < 0.01:
    print("✅ 分箱逻辑一致：_calc_margin_change_v2 和详细报告使用相同的分箱标准")
else:
    print("❌ 分箱逻辑不一致，需要修复")
print()
