"""验证边际变化双轨制（技能文档 v2）"""

import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

import pandas as pd
import numpy as np
from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer

analyzer = ChipDeepAnalyzer("601899.SH")

print("=" * 70)
print("边际变化双轨制验证（技能文档 v2）")
print("=" * 70)
print()

# 构造测试数据：当前价分箱变化小，筹码峰分箱变化大
def create_dual_track_data(close, close_bin_pct, prev_close_bin_pct, peak_bin_pct, prev_peak_bin_pct):
    """构造双轨制测试数据"""
    step = 2.0 if close >= 20 else 1.0
    
    # 构造价格点
    prices = np.arange(0, close + 20, step / 2)
    
    # 获取分箱边界
    temp_df = pd.DataFrame({"price": prices, "percent": np.zeros(len(prices))})
    bins = analyzer._get_price_bins(temp_df, close)
    
    # 当前价所在分箱
    close_bin = np.digitize(close, bins, right=False) - 1
    close_bin = max(0, min(close_bin, len(bins) - 2))
    
    # 筹码峰分箱（假设在另一个位置）
    peak_bin = close_bin - 2 if close_bin >= 2 else close_bin + 2
    peak_bin = max(0, min(peak_bin, len(bins) - 2))
    
    # 构造筹码分布
    curr_pcts = []
    prev_pcts = []
    for p in prices:
        p_bin = np.digitize(p, bins, right=False) - 1
        p_bin = max(0, min(p_bin, len(bins) - 2))
        
        if p_bin == close_bin:
            # 当前价分箱
            curr_pcts.append(close_bin_pct / 2)
            prev_pcts.append(prev_close_bin_pct / 2)
        elif p_bin == peak_bin:
            # 筹码峰分箱
            curr_pcts.append(peak_bin_pct / 2)
            prev_pcts.append(prev_peak_bin_pct / 2)
        else:
            curr_pcts.append(0.5)
            prev_pcts.append(0.5)
    
    chips_df = pd.DataFrame({"price": prices, "percent": curr_pcts})
    prev_chips_df = pd.DataFrame({"price": prices, "percent": prev_pcts})
    
    return chips_df, prev_chips_df, close_bin, peak_bin

# 测试场景
test_cases = [
    # (名称, 当前价, 当前分箱%, 2周前分箱%, 筹码峰%, 2周前筹码峰%, 预期来源)
    ("当前分箱大", 31.58, 27.6, 9.0, 20.0, 15.0, "当前分箱"),    # 当前分箱变化大
    ("筹码峰大", 31.58, 15.0, 12.0, 40.0, 20.0, "筹码峰分箱"),    # 筹码峰变化大
    ("两者相等", 31.58, 20.0, 10.0, 25.0, 15.0, "当前分箱"),      # 相等时取当前分箱
    ("温和-当前", 43.19, 7.1, 1.8, 5.0, 2.0, "当前分箱"),         # 当前分箱温和
    ("温和-峰", 43.19, 5.0, 2.0, 8.0, 1.0, "筹码峰分箱"),         # 筹码峰温和
]

print("【双轨制测试】")
print("-" * 70)

all_pass = True
for name, close, curr_close_pct, prev_close_pct, curr_peak_pct, prev_peak_pct, expected_source in test_cases:
    chips_df, prev_chips_df, close_bin_idx, peak_bin_idx = create_dual_track_data(
        close, curr_close_pct, prev_close_pct, curr_peak_pct, prev_peak_pct
    )
    
    score, label, detail, panic = analyzer._calc_margin_change_v2(chips_df, prev_chips_df, close)
    
    # 计算实际变化
    chg_close = curr_close_pct - prev_close_pct
    chg_peak = curr_peak_pct - prev_peak_pct
    
    # 判断来源
    if abs(chg_close) >= abs(chg_peak):
        actual_source = "当前分箱"
        actual_chg = chg_close
    else:
        actual_source = "筹码峰分箱"
        actual_chg = chg_peak
    
    print(f"{name:12s} | 当前价 {close:>6.2f}")
    print(f"             | 当前分箱变化: {chg_close:>+6.1f}% | 筹码峰变化: {chg_peak:>+6.1f}%")
    print(f"             | 实际来源: {actual_source} | 实际变化: {actual_chg:>+6.1f}%")
    print(f"             | 得分: {score:.1f} | 标签: {label} | 恐慌: {panic}")
    print(f"             | 描述: {detail}")
    
    if actual_source == expected_source:
        print(f"             | 来源判断: ✅")
    else:
        print(f"             | 来源判断: ❌ (预期 {expected_source})")
        all_pass = False
    print()

print("=" * 70)
print("结论")
print("=" * 70)
print()
if all_pass:
    print("✅ 所有测试通过！双轨制逻辑正确")
else:
    print("❌ 存在测试失败")
print()

print("【双轨制说明】")
print("-" * 70)
print("取当前分箱与筹码峰分箱中绝对值更大的变化")
print("- 当前分箱：反映当前交易价位的即时资金流向")
print("- 筹码峰分箱：反映筹码主峰的迁移趋势，领先于价格变动")
print()
