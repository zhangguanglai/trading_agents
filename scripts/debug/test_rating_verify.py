"""验证紫金矿业601899的评级计算"""

import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer

analyzer = ChipDeepAnalyzer("601899.SH")

print("=" * 70)
print("紫金矿业 601899 评级验证")
print("=" * 70)
print()

# 模拟紫金矿业的六维评分数据（根据截图）
dim6 = {
    "chip_density": {"score": 0.5, "label": "⚠️", "detail": "中等支撑"},
    "margin_change": {"score": 0.5, "label": "⚠️", "detail": "被动承接（下方增加）"},
    "winner_position": {"score": 0.5, "label": "✅", "detail": "均衡健康"},
    "cost_rise": {"score": 0.5, "label": "✅✅", "detail": "底部大幅抬高"},
    "overshoot": {"score": 0.25, "label": "⚠️", "detail": "略高于成本"},
    "support_level": {"score": 0.5, "label": "✅", "detail": "层级良好"},
    "total": 0.5 + 0.5 + 0.5 + 0.5 + 0.25 + 0.5  # = 2.75
}

print("【六维评分】")
print("-" * 70)
for key, item in dim6.items():
    if key != "total":
        print(f"  {key}: score={item['score']}, label={item['label']}, detail={item['detail']}")
print(f"  TOTAL: {dim6['total']:.2f}")
print()

print("【评级计算】")
print("-" * 70)

# 计算基础评级
base_rating = analyzer._calc_base_rating(dim6["total"])
print(f"  dim6['total'] = {dim6['total']:.2f}")
print(f"  _calc_base_rating({dim6['total']:.2f}) = {base_rating} (⭐{'⭐' * (base_rating - 1)})")
print()

# 检查否决项
print("【否决项检查】")
print("-" * 70)
print(f"  筹码密度 label = {dim6['chip_density']['label']} {'❌ 触发否决!' if dim6['chip_density']['label'] == '❌' else '✅ 无否决'}")
print(f"  边际变化 label = {dim6['margin_change']['label']}, detail = {dim6['margin_change']['detail']}")
print(f"    → '恐慌出逃' in detail: {'❌ 触发否决!' if '恐慌出逃' in dim6['margin_change']['detail'] else '✅ 无否决'}")
print(f"  成本抬升 label = {dim6['cost_rise']['label']} {'❌ 触发否决!' if dim6['cost_rise']['label'] == '❌' else '✅ 无否决'}")
print(f"  获利盘 label = {dim6['winner_position']['label']}, detail = {dim6['winner_position']['detail']}")
print(f"    → '劣质低胜率' in detail: {'❌ 触发否决!' if '劣质低胜率' in dim6['winner_position']['detail'] else '✅ 无否决'}")
print()

# 应用否决规则
max_rating = analyzer._apply_veto_rules(dim6)
print(f"  _apply_veto_rules 返回: max_rating = {max_rating} (⭐{'⭐' * (max_rating - 1)})")
print()

# 最终评级
final_rating = min(base_rating, max_rating)
print("【最终结果】")
print("-" * 70)
print(f"  base_rating = {base_rating}")
print(f"  max_rating = {max_rating}")
print(f"  final_rating = min({base_rating}, {max_rating}) = {final_rating}")
print(f"  评级: {'⭐' * final_rating}")
print()

print("【截图数据对照】")
print("-" * 70)
print("  截图显示: 六维评分 3.75/5.5, 评级 ⭐⭐")
print(f"  计算结果: 六维评分 {dim6['total']:.2f}/5.5, 评级 {'⭐' * final_rating}")
print()

if final_rating == 2:
    print("❌ 不一致！应该显示 ⭐⭐⭐ (3星)")
else:
    print("✅ 一致")
