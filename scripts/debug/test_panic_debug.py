"""调试恐慌出逃检测"""

import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

import pandas as pd
import numpy as np
from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer

analyzer = ChipDeepAnalyzer("601899.SH")

print("=" * 70)
print("恐慌出逃检测调试")
print("=" * 70)
print()

# 构造恐慌出逃场景
close = 25.0
step = 2.0
bin_low = 24.0
bin_high = 26.0

# 价格点
prices = np.arange(0, close + 20, step / 2)

# 当前筹码：分箱内 20%，下方 35%，上方 10%
curr_pcts = []
for p in prices:
    if bin_low <= p < bin_high:
        curr_pcts.append(10.0)  # 分箱内：20% / 2 = 10%
    elif p < close:
        curr_pcts.append(35.0 / len([x for x in prices if x < close and not (bin_low <= x < bin_high)]))
    else:
        curr_pcts.append(0.1)

# 2周前筹码：分箱内 40%，下方 20%，上方 10%
prev_pcts = []
for p in prices:
    if bin_low <= p < bin_high:
        prev_pcts.append(20.0)  # 分箱内：40% / 2 = 20%
    elif p < close:
        prev_pcts.append(20.0 / len([x for x in prices if x < close and not (bin_low <= x < bin_high)]))
    else:
        prev_pcts.append(0.1)

chips_df = pd.DataFrame({"price": prices, "percent": curr_pcts})
prev_chips_df = pd.DataFrame({"price": prices, "percent": prev_pcts})

print("【数据检查】")
print("-" * 70)

# 计算分箱变化
curr_mask = (chips_df["price"] >= bin_low) & (chips_df["price"] < bin_high)
prev_mask = (prev_chips_df["price"] >= bin_low) & (prev_chips_df["price"] < bin_high)
chg = chips_df.loc[curr_mask, "percent"].sum() - prev_chips_df.loc[prev_mask, "percent"].sum()

print(f"当前价: {close}")
print(f"分箱: [{bin_low}, {bin_high})")
print(f"分箱变化: {chg:.1f}%")
print()

# 计算下方变化
below_curr_mask = chips_df["price"] < close
below_prev_mask = prev_chips_df["price"] < close
below_curr_pct = chips_df.loc[below_curr_mask, "percent"].sum()
below_prev_pct = prev_chips_df.loc[below_prev_mask, "percent"].sum()
below_chg = below_curr_pct - below_prev_pct

print(f"下方当前: {below_curr_pct:.1f}%")
print(f"下方2周前: {below_prev_pct:.1f}%")
print(f"下方变化: {below_chg:.1f}%")
print()

print("【判定树执行】")
print("-" * 70)

if chg > 10:
    print("1. chg > 10% → 猛烈向上")
elif chg >= 3:
    print("2. chg >= 3% → 温和")
elif chg < -15:
    print("3. chg < -15% → 检查下方分箱")
    print(f"   下方变化: {below_chg:.1f}%")
    if below_chg > 10:
        print(f"   below_chg > 10% → 恐慌出逃 ✅")
    else:
        print(f"   below_chg <= 10% → 大幅减少")
elif chg < 0:
    print("4. chg < 0 → 减少")
else:
    print("5. |chg| < 3% → 无人")

print()

# 调用实际方法
score, label, detail, panic = analyzer._calc_margin_change_v2(chips_df, prev_chips_df, close)
print(f"【实际结果】")
print(f"得分: {score:.1f}")
print(f"标签: {label}")
print(f"描述: {detail}")
print(f"恐慌: {panic}")
print()

print("【预期结果】")
print(f"得分: 0.0")
print(f"标签: ❌")
print(f"描述: 恐慌出逃")
print(f"恐慌: True")
