"""调试000960的六维评分计算"""

import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

import pandas as pd
from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer

# 创建模拟数据，模拟000960的情况
# 从截图推断：
# - 当前价 43.19
# - 平均成本 39.98
# - 获利盘 99.1%
# - 边际变化 +1.9%，向上集中但幅度不够（<3%）→ 0分 ❌
# - 筹码密度 31.3% → 0.5分 ⚠️（20%~40%之间）
# - 获利盘 99.1% > 90% → 0分 ❌⚠️
# - 成本抬升 93.8% > 30% → 0.5分 ✅✅
# - 超跌程度 +8.0%（>3%且<15%）→ 0.25分 ⚠️
# - 下方支撑 4层 → 0.5分 ✅

# 预期总分：0 + 0.5 + 0 + 0.5 + 0.25 + 0.5 = 1.75

print("=" * 70)
print("000960 六维评分调试")
print("=" * 70)
print()

# 手动验证各维度得分
scores = {
    "边际变化": 0.0,
    "筹码密度": 0.5,
    "获利盘": 0.0,
    "成本抬升": 0.5,
    "超跌程度": 0.25,
    "下方支撑": 0.5,
}

total = sum(scores.values())
print("【手动计算】")
for dim, score in scores.items():
    print(f"  {dim}: {score}")
print(f"  总分: {total}")
print()

# 验证评级
analyzer = ChipDeepAnalyzer("000960.SZ")
rating = analyzer._calc_base_rating(total)
print(f"【评级映射】总分 {total} → {rating}星 (⭐{'⭐' * (rating - 1) if rating > 1 else ''})")
print()

# 检查否决项
dim6 = {
    "chip_density": {"score": 0.5, "label": "⚠️", "detail": "中等支撑"},
    "margin_change": {"score": 0.0, "label": "❌", "detail": "无人承接"},
    "winner_position": {"score": 0.0, "label": "❌⚠️", "detail": "极度过热"},
    "cost_rise": {"score": 0.5, "label": "✅✅", "detail": "底部大幅抬高"},
    "overshoot": {"score": 0.25, "label": "⚠️", "detail": "略高于成本"},
    "support_level": {"score": 0.5, "label": "✅", "detail": "层级良好"},
}

max_rating = analyzer._apply_veto_rules(dim6)
final_rating = min(rating, max_rating)
print(f"【否决项检查】")
print(f"  基础评级: {rating}星")
print(f"  否决后最高: {max_rating}星")
print(f"  最终评级: {final_rating}星")
print()

# 检查 _generate_summary 中的逻辑问题
print("=" * 70)
print("检查 _generate_summary 中的逻辑问题")
print("=" * 70)
print()

# 问题1: overshoot score 为 0.25 时，显示"当前价低于平均成本"
# 但 overshoot = +8.0% 是高于成本！
print("【问题1】底部特征判断逻辑错误")
print("-" * 70)
print("  代码逻辑:")
print("    if dim6['overshoot']['score']:")
print("        bottom_signals.append('当前价低于平均成本')")
print()
print("  当前数据:")
print("    overshoot_score = 0.25 (truthy)")
print("    overshoot = +8.0% (高于成本，不是低于！)")
print()
print("  问题: score 为 truthy 就显示'低于'，但实际是'高于'")
print("  应该检查 overshoot 的实际值，而不是 score")
print()

# 问题2: 总分显示
print("【问题2】总分显示")
print("-" * 70)
print(f"  _calc_dim6 计算总分: {total}")
print(f"  _generate_summary 使用: dim6['total'] = {total}")
print(f"  显示格式: {total:.1f}/5.5")
print()

# 如果总分是 1.75，显示应该是 1.8（四舍五入）
# 如果总分是 2.25，显示应该是 2.3（四舍五入）
print(f"  如果总分=1.75: 显示 1.8/5.5")
print(f"  如果总分=2.25: 显示 2.3/5.5")
print()
print("  截图显示 1.8/5.5，说明实际总分可能是 1.75")
print()

print("=" * 70)
print("结论")
print("=" * 70)
print()
print("1. 截图中的'1.8/5.5'可能是旧版本数据（等权评分时代的残留）")
print("2. 当前加权评分计算应该是 2.25/5.5")
print("3. _generate_summary 中的'当前价低于平均成本'判断逻辑有误")
print("   应该基于 overshoot 实际值判断，而不是 score 的 truthy 值")
print()
