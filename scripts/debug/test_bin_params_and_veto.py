"""验证分箱参数和否决项逻辑（技能文档 v2 标准）"""

import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

import pandas as pd
from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer

analyzer = ChipDeepAnalyzer("000001.SZ")

print("=" * 70)
print("分箱参数验证（技能文档标准）")
print("=" * 70)
print()

# 测试分箱参数
test_prices = [9.5, 15.0, 25.0, 50.0, 120.0, 200.0]
print("【分箱粒度和搜索范围】")
print("-" * 70)
for price in test_prices:
    step, search_range = analyzer._get_bin_params(price)
    tier = "低价股(<20)" if price < 20 else ("中价股(20~100)" if price < 100 else "高价股(>100)")
    print(f"  股价 {price:.1f}元 ({tier}): 分箱粒度={step}元, 搜索范围=±{search_range}元")

print()
print("【技能文档标准对照】")
print("-" * 70)
print("  股价 < 20元:   step=1.0, search_range=±1.5")
print("  股价 20~100元: step=2.0, search_range=±3.0")
print("  股价 > 100元:  step=5.0, search_range=±8.0")

print()
print("=" * 70)
print("恐慌出逃否决项验证")
print("=" * 70)
print()

# 构造测试数据：恐慌出逃场景（上方减>15% + 下方增>10%）
# 当前筹码：上方30%，下方70%
# 前期筹码：上方50%，下方50%
# 上方变化：30-50 = -20% (< -15%)
# 下方变化：70-50 = +20% (> +10%)
chips_df_panic = pd.DataFrame({
    "price": [8.0, 9.0, 10.0, 11.0, 12.0],
    "percent": [20.0, 25.0, 25.0, 15.0, 15.0]  # 下方70%，上方30%
})
prev_chips_df_panic = pd.DataFrame({
    "price": [8.0, 9.0, 10.0, 11.0, 12.0],
    "percent": [15.0, 20.0, 15.0, 25.0, 25.0]  # 下方50%，上方50%
})

close_price = 10.0
is_panic = analyzer._check_panic_exit(chips_df_panic, prev_chips_df_panic, close_price)
print(f"【恐慌出逃场景】上方50%→30%(-20%), 下方50%→70%(+20%)")
print(f"  当前价={close_price}, 恐慌出逃判定: {'✅ 触发' if is_panic else '❌ 未触发'}")
print(f"  预期: ✅ 触发（上方减20%>15%，下方增20%>10%）")

print()

# 构造测试数据：正常场景（不满足恐慌出逃）
chips_df_normal = pd.DataFrame({
    "price": [8.0, 9.0, 10.0, 11.0, 12.0],
    "percent": [15.0, 20.0, 30.0, 20.0, 15.0]  # 下方55%，上方35%
})
prev_chips_df_normal = pd.DataFrame({
    "price": [8.0, 9.0, 10.0, 11.0, 12.0],
    "percent": [15.0, 20.0, 25.0, 22.0, 18.0]  # 下方60%，上方40%
})

is_panic_normal = analyzer._check_panic_exit(chips_df_normal, prev_chips_df_normal, close_price)
print(f"【正常场景】上方40%→35%(-5%), 下方60%→55%(-5%)")
print(f"  当前价={close_price}, 恐慌出逃判定: {'✅ 触发' if is_panic_normal else '❌ 未触发'}")
print(f"  预期: ❌ 未触发")

print()
print("=" * 70)
print("否决项综合验证")
print("=" * 70)
print()

# 模拟 dim6 数据
dim6_veto_density = {
    "chip_density": {"score": 0, "label": "❌", "detail": "薄支撑"},
    "margin_change": {"score": 2.0, "label": "✅", "detail": "猛烈承接"},
    "winner_position": {"score": 1.0, "label": "✅", "detail": "均衡"},
    "cost_rise": {"score": 0.5, "label": "✅", "detail": "底部抬高"},
    "overshoot": {"score": 0.5, "label": "✅", "detail": "正常波动"},
    "support_level": {"score": 0.5, "label": "✅", "detail": "层级良好"},
}

max_rating_density = analyzer._apply_veto_rules(dim6_veto_density, chips_df_normal, prev_chips_df_normal, close_price)
print(f"【筹码密度❌否决】总分=4.5, 否决后最高评级={max_rating_density}星")
print(f"  预期: 2星")

dim6_veto_cost = {
    "chip_density": {"score": 1.0, "label": "✅", "detail": "厚垫子"},
    "margin_change": {"score": 2.0, "label": "✅", "detail": "猛烈承接"},
    "winner_position": {"score": 1.0, "label": "✅", "detail": "均衡"},
    "cost_rise": {"score": 0, "label": "❌", "detail": "底部基本没变"},
    "overshoot": {"score": 0.5, "label": "✅", "detail": "正常波动"},
    "support_level": {"score": 0.5, "label": "✅", "detail": "层级良好"},
}

max_rating_cost = analyzer._apply_veto_rules(dim6_veto_cost, chips_df_normal, prev_chips_df_normal, close_price)
print(f"【成本抬升❌否决】总分=4.0, 否决后最高评级={max_rating_cost}星")
print(f"  预期: 2星")

dim6_veto_panic = {
    "chip_density": {"score": 1.0, "label": "✅", "detail": "厚垫子"},
    "margin_change": {"score": 0, "label": "❌", "detail": "恐慌出逃"},
    "winner_position": {"score": 1.0, "label": "✅", "detail": "均衡"},
    "cost_rise": {"score": 0.5, "label": "✅", "detail": "底部抬高"},
    "overshoot": {"score": 0.5, "label": "✅", "detail": "正常波动"},
    "support_level": {"score": 0.5, "label": "✅", "detail": "层级良好"},
}

max_rating_panic = analyzer._apply_veto_rules(dim6_veto_panic, chips_df_panic, prev_chips_df_panic, close_price)
print(f"【恐慌出逃否决】总分=3.5, 否决后最高评级={max_rating_panic}星")
print(f"  预期: 2星")

dim6_no_veto = {
    "chip_density": {"score": 1.0, "label": "✅", "detail": "厚垫子"},
    "margin_change": {"score": 2.0, "label": "✅", "detail": "猛烈承接"},
    "winner_position": {"score": 1.0, "label": "✅", "detail": "均衡"},
    "cost_rise": {"score": 0.5, "label": "✅", "detail": "底部抬高"},
    "overshoot": {"score": 0.5, "label": "✅", "detail": "正常波动"},
    "support_level": {"score": 0.5, "label": "✅", "detail": "层级良好"},
}

max_rating_no = analyzer._apply_veto_rules(dim6_no_veto, chips_df_normal, prev_chips_df_normal, close_price)
print(f"【无否决项】总分=5.5, 否决后最高评级={max_rating_no}星")
print(f"  预期: 5星")

print()
print("=" * 70)
print("验证完成！")
print("=" * 70)
