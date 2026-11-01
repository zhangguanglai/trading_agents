"""验证一句话总结生成逻辑"""

import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer

analyzer = ChipDeepAnalyzer("000960.SZ")

print("=" * 70)
print("一句话总结生成验证")
print("=" * 70)
print()

# 场景1：000960（截图数据）
# 当前价 43.19，成本 39.98，获利盘 99.1%，总分 1.75，2星
print("【场景1】000960 —— 高获利盘，评分偏低")
print("-" * 70)
dim6_1 = {
    "chip_density": {"score": 0.5, "label": "⚠️", "detail": "中等支撑"},
    "margin_change": {"score": 0.0, "label": "❌", "detail": "无人承接"},
    "winner_position": {"score": 0.0, "label": "❌⚠️", "detail": "极度过热"},
    "cost_rise": {"score": 0.5, "label": "✅✅", "detail": "底部大幅抬高"},
    "overshoot": {"score": 0.25, "label": "⚠️", "detail": "略高于成本"},
    "support_level": {"score": 0.5, "label": "✅", "detail": "层级良好"},
}
one_liner = analyzer._generate_one_liner(43.19, 39.98, 99.1, dim6_1, 1.75, 8.0)
print(one_liner)
print()

# 场景2：锡业股份（范例）
# 获利盘 42.21% 均衡，价格 38.8 贴合成本 38.1（+1.8%），总分 3.75，3星
print("【场景2】锡业股份 —— 均衡健康，中性偏持有")
print("-" * 70)
dim6_2 = {
    "chip_density": {"score": 0.5, "label": "⚠️", "detail": "中等支撑"},
    "margin_change": {"score": 0.5, "label": "⚠️", "detail": "温和承接"},
    "winner_position": {"score": 0.5, "label": "✅", "detail": "均衡健康"},
    "cost_rise": {"score": 0.25, "label": "⚠️", "detail": "成本抬升有限"},
    "overshoot": {"score": 0.25, "label": "⚠️", "detail": "几乎贴合成本"},
    "support_level": {"score": 0.5, "label": "✅", "detail": "层级良好"},
}
one_liner = analyzer._generate_one_liner(38.8, 38.1, 42.21, dim6_2, 3.75, 1.8)
print(one_liner)
print()

# 场景3：优质标的 —— 4星
print("【场景3】优质标的 —— 4星，积极看多")
print("-" * 70)
dim6_3 = {
    "chip_density": {"score": 1.0, "label": "✅", "detail": "高度集中"},
    "margin_change": {"score": 2.0, "label": "✅", "detail": "猛烈承接"},
    "winner_position": {"score": 0.5, "label": "✅", "detail": "均衡健康"},
    "cost_rise": {"score": 0.5, "label": "✅✅", "detail": "底部大幅抬高"},
    "overshoot": {"score": 0.5, "label": "✅", "detail": "低于成本"},
    "support_level": {"score": 0.5, "label": "✅", "detail": "层级良好"},
}
one_liner = analyzer._generate_one_liner(35.0, 38.0, 45.0, dim6_3, 4.5, -7.9)
print(one_liner)
print()

# 场景4：恐慌标 —— 1星
print("【场景4】恐慌标的 —— 1星，建议回避")
print("-" * 70)
dim6_4 = {
    "chip_density": {"score": 0.0, "label": "❌", "detail": "分散"},
    "margin_change": {"score": 0.0, "label": "❌", "detail": "无人承接"},
    "winner_position": {"score": 0.0, "label": "❌", "detail": "极度恐慌"},
    "cost_rise": {"score": 0.0, "label": "❌", "detail": "成本未抬升"},
    "overshoot": {"score": 0.0, "label": "❌", "detail": "大幅低于成本"},
    "support_level": {"score": 0.0, "label": "❌", "detail": "无支撑"},
}
one_liner = analyzer._generate_one_liner(25.0, 35.0, 5.0, dim6_4, 0.5, -28.6)
print(one_liner)
print()

print("=" * 70)
print("验证完成")
print("=" * 70)
