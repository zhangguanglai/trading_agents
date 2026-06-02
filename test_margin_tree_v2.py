"""验证边际变化完整判定树（技能文档 v2）"""

import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

import pandas as pd
from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer

analyzer = ChipDeepAnalyzer("601899.SH")

print("=" * 70)
print("边际变化完整判定树验证（技能文档 v2）")
print("=" * 70)
print()

# 测试场景定义
test_cases = [
    # (名称, 当前价, 当前分箱%, 2周前分箱%, 下方变化%, 预期得分, 预期标签, 预期恐慌)
    ("紫金矿业", 31.58, 27.6, 9.0, 5.0, 2.0, "✅", False),    # chg=+18.6% > 10
    ("绿色动力", 9.71, 60.6, 47.3, 3.0, 2.0, "✅", False),     # chg=+13.3% > 10
    ("锡业股份", 43.19, 7.1, 1.8, 2.0, 0.5, "⚠️", False),     # chg=+5.2% >= 3
    ("招商银行", 39.5, 41.2, 43.6, -1.0, 0, "❌", False),     # chg=-2.4% |chg|<3
    ("中国神华", 49.5, 30.8, 31.8, 0.5, 0, "❌", False),      # chg=-1.0% |chg|<3
    ("中国重汽", 23.0, 44.6, 54.4, -5.0, 0, "❌", False),     # chg=-9.8% -15<chg<0
    ("沃尔核材", 22.36, 40.4, 79.6, 15.0, 0, "❌", True),     # chg=-39.2% < -15, 下方+15%
    ("恐慌出逃", 25.0, 20.0, 40.0, 12.0, 0, "❌", True),      # chg=-20.0% < -15, 下方+12%
    ("大幅减少", 25.0, 20.0, 40.0, 5.0, 0, "❌", False),      # chg=-20.0% < -15, 下方+5%
]

print("【判定树测试】")
print("-" * 70)

all_pass = True
for name, close, curr_pct, prev_pct, below_chg, expected_score, expected_label, expected_panic in test_cases:
    # 构造测试数据
    chips_df = pd.DataFrame({
        "price": [close - 5, close - 3, close - 1, close, close + 1, close + 3, close + 5],
        "percent": [5.0, 10.0, curr_pct * 0.3, curr_pct * 0.4, curr_pct * 0.3, 10.0, 5.0]
    })
    
    prev_chips_df = pd.DataFrame({
        "price": [close - 5, close - 3, close - 1, close, close + 1, close + 3, close + 5],
        "percent": [5.0 - below_chg * 0.2, 10.0 - below_chg * 0.3, prev_pct * 0.3, prev_pct * 0.4, prev_pct * 0.3, 10.0, 5.0]
    })
    
    # 调整使分箱变化正确
    actual_chg = curr_pct - prev_pct
    
    score, label, detail, panic = analyzer._calc_margin_change_v2(chips_df, prev_chips_df, close)
    
    print(f"{name:8s} | 当前价 {close:>6.2f} | chg={actual_chg:>+6.1f}%")
    print(f"         | 得分: {score:.1f} (预期 {expected_score:.1f}) | 标签: {label} (预期 {expected_label}) | 恐慌: {panic} (预期 {expected_panic})")
    print(f"         | 描述: {detail}")
    
    if score == expected_score and label == expected_label and panic == expected_panic:
        print(f"         | 结果: ✅")
    else:
        print(f"         | 结果: ❌")
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

print("【判定树总结】")
print("-" * 70)
print("chg > 10%        → 2.0分 ✅ 猛烈向上")
print("chg >= 3%        → 0.5分 ⚠️ 温和")
print("chg < -15%       → 检查下方分箱")
print("  下方大增 >10%  → 0分 ❌ 恐慌出逃 + 否决项")
print("  下方无大增     → 0分 ❌ 减少")
print("chg < 0          → 0分 ❌ 减少")
print("|chg| < 3%       → 0分 ❌ 无人")
print()
