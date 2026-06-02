"""验证边际变化完整判定树（技能文档 v2）- v5 最终版"""

import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

import pandas as pd
import numpy as np
from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer

analyzer = ChipDeepAnalyzer("601899.SH")

print("=" * 70)
print("边际变化完整判定树验证（技能文档 v2）")
print("=" * 70)
print()

# 直接使用 _get_price_bins 和 _get_current_bin 构造数据
def create_test_data(close, curr_bin_pct, prev_bin_pct, below_curr_pct, below_prev_pct):
    """构造测试数据"""
    step = 2.0 if close >= 20 else 1.0
    
    # 使用 analyzer 的方法获取分箱
    # 构造一个足够大的价格范围
    prices = np.arange(0, close + 20, step / 2)
    
    # 获取分箱边界
    temp_df = pd.DataFrame({"price": prices, "percent": np.zeros(len(prices))})
    bins = analyzer._get_price_bins(temp_df, close)
    
    # 获取当前价所在分箱
    bin_low, bin_high = analyzer._get_current_bin(temp_df, close)
    
    # 构造筹码分布
    curr_pcts = []
    prev_pcts = []
    for p in prices:
        if bin_low <= p < bin_high:
            # 分箱内：均匀分布
            curr_pcts.append(curr_bin_pct / 2)
            prev_pcts.append(prev_bin_pct / 2)
        elif p < close:
            # 下方
            curr_pcts.append(below_curr_pct / len([x for x in prices if x < close and not (bin_low <= x < bin_high)]))
            prev_pcts.append(below_prev_pct / len([x for x in prices if x < close and not (bin_low <= x < bin_high)]))
        else:
            # 上方
            curr_pcts.append(0.1)
            prev_pcts.append(0.1)
    
    chips_df = pd.DataFrame({"price": prices, "percent": curr_pcts})
    prev_chips_df = pd.DataFrame({"price": prices, "percent": prev_pcts})
    
    return chips_df, prev_chips_df

# 测试场景
test_cases = [
    # (名称, 当前价, 当前分箱%, 2周前分箱%, 当前下方%, 2周前下方%, 预期得分, 预期标签, 预期恐慌)
    ("猛烈向上", 31.58, 27.6, 9.0, 30.0, 30.0, 2.0, "✅", False),
    ("温和", 43.19, 7.1, 1.8, 20.0, 20.0, 0.5, "⚠️", False),
    ("无人", 39.5, 41.2, 43.6, 20.0, 20.0, 0, "❌", False),
    ("减少", 23.0, 44.6, 54.4, 20.0, 20.0, 0, "❌", False),
    ("恐慌出逃", 25.0, 20.0, 40.0, 35.0, 20.0, 0, "❌", True),
    ("大幅减少", 25.0, 20.0, 40.0, 25.0, 20.0, 0, "❌", False),
]

print("【判定树测试】")
print("-" * 70)

all_pass = True
for name, close, curr_bin_pct, prev_bin_pct, below_curr_pct, below_prev_pct, expected_score, expected_label, expected_panic in test_cases:
    chips_df, prev_chips_df = create_test_data(close, curr_bin_pct, prev_bin_pct, below_curr_pct, below_prev_pct)
    
    score, label, detail, panic = analyzer._calc_margin_change_v2(chips_df, prev_chips_df, close)
    
    # 计算实际值
    bin_low, bin_high = analyzer._get_current_bin(chips_df, close)
    curr_mask = (chips_df["price"] >= bin_low) & (chips_df["price"] < bin_high)
    prev_mask = (prev_chips_df["price"] >= bin_low) & (prev_chips_df["price"] < bin_high)
    actual_chg = chips_df.loc[curr_mask, "percent"].sum() - prev_chips_df.loc[prev_mask, "percent"].sum()
    
    below_curr_mask = chips_df["price"] < close
    below_prev_mask = prev_chips_df["price"] < close
    actual_below_chg = chips_df.loc[below_curr_mask, "percent"].sum() - prev_chips_df.loc[below_prev_mask, "percent"].sum()
    
    print(f"{name:10s} | 当前价 {close:>6.2f} | 分箱 [{bin_low:.0f}, {bin_high:.0f}) | chg={actual_chg:>+6.1f}% | 下方={actual_below_chg:>+6.1f}%")
    print(f"           | 得分: {score:.1f} (预期 {expected_score:.1f}) | 标签: {label} (预期 {expected_label}) | 恐慌: {panic} (预期 {expected_panic})")
    print(f"           | 描述: {detail}")
    
    if score == expected_score and label == expected_label and panic == expected_panic:
        print(f"           | 结果: ✅")
    else:
        print(f"           | 结果: ❌")
        all_pass = False
    print()

print("=" * 70)
print("结论")
print("=" * 70)
print()
if all_pass:
    print("✅ 所有测试通过！判定树逻辑正确")
else:
    print("❌ 存在测试失败")
print()
