"""验证加权评分与评级映射关系（技能文档 v2 标准）"""

import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer

# 创建模拟的 analyzer 实例
analyzer = ChipDeepAnalyzer("000001.SZ")

print("=" * 70)
print("加权评分与评级映射关系验证（技能文档 v2 标准）")
print("=" * 70)
print()

# 测试各分档（加权总分范围 0~5.5）
test_cases = [
    {"total": 5.5, "desc": "满分（边际✅2.0 + 其他全✅）"},
    {"total": 5.0, "desc": "优秀（触发5星阈值）"},
    {"total": 4.5, "desc": "良好偏上"},
    {"total": 4.0, "desc": "良好（触发4星阈值）"},
    {"total": 3.0, "desc": "中等偏上"},
    {"total": 2.5, "desc": "中性（触发3星阈值）"},
    {"total": 2.0, "desc": "中等偏下"},
    {"total": 1.5, "desc": "较弱"},
    {"total": 1.0, "desc": "差（触发2星阈值）"},
    {"total": 0.5, "desc": "很差"},
    {"total": 0.0, "desc": "极差"},
]

print("【基础映射（无否决项）】")
print("-" * 70)
for case in test_cases:
    total = case["total"]
    rating = analyzer._calc_base_rating(total)
    stars = "⭐" * rating
    print(f"  {total:.1f}/5.5 → {stars} ({rating}星) | {case['desc']}")

print()
print("【技能文档 v2 评级映射标准】")
print("-" * 70)
print("  ≥ 5.0 → ⭐⭐⭐⭐⭐ (5星) 最强底部信号")
print("  ≥ 4.0 → ⭐⭐⭐⭐ (4星) 高度指向底部")
print("  ≥ 2.5 → ⭐⭐⭐ (3星) 中性")
print("  ≥ 1.0 → ⭐⭐ (2星) 偏空")
print("  < 1.0 → ⭐ (1星) 回避")

print()
print("【否决项规则】")
print("-" * 70)
print("  当以下任一条件满足时，评级最高限制为 ⭐⭐（2星）：")
print("  • 筹码密度 ❌（集中度 < 20%）")
print("  • 边际变化 ❌ 且 恐慌出逃（上方减>15% + 下方增>10%）")
print("  • 成本抬升 ❌（< 5%）")
print("  • 劣质低胜率 ❌（年度涨幅<25% + 换手<90% + 成本抬升<15%）")

print()
print("【技能文档案例验证】")
print("-" * 70)

# 技能文档中的加权案例
scenarios = [
    {
        "name": "锡业股份(过热)",
        "scores": {"margin": 0.5, "density": 1.0, "winner": 0.0, "cost": 0.5, "overshoot": 0.25, "support": 0.5},
        "desc": "边际⚠️(0.5)+筹码✅(1)+获利❌(0)+成本✅(0.5)+超跌⚠️(0.25)+支撑✅(0.5)"
    },
    {
        "name": "绿色动力(过热)",
        "scores": {"margin": 2.0, "density": 1.0, "winner": 0.0, "cost": 0.5, "overshoot": 0.25, "support": 0.5},
        "desc": "边际✅(2.0)+筹码✅(1)+获利❌(0)+成本✅(0.5)+超跌⚠️(0.25)+支撑✅(0.5)"
    },
    {
        "name": "中国神华(锁仓)",
        "scores": {"margin": 0.0, "density": 1.0, "winner": 0.0, "cost": 0.25, "overshoot": 0.25, "support": 0.25},
        "desc": "边际❌(0)+筹码✅(1)+获利❌(0)+成本⚠️(0.25)+超跌⚠️(0.25)+支撑⚠️(0.25)"
    },
    {
        "name": "友发集团(劣质)",
        "scores": {"margin": 0.5, "density": 1.0, "winner": 0.0, "cost": 0.25, "overshoot": 0.5, "support": 0.5},
        "desc": "边际⚠️(0.5)+筹码✅(1)+获利❌(0)+成本⚠️(0.25)+超跌✅(0.5)+支撑✅(0.5)"
    },
]

for s in scenarios:
    total = sum(s["scores"].values())
    rating = analyzer._calc_base_rating(total)
    stars = "⭐" * rating
    print(f"  {s['name']}:")
    print(f"    计算: {s['desc']}")
    print(f"    总分: {total:.2f}/5.5 → {stars} ({rating}星)")
    print()

print("=" * 70)
print("验证完成！")
print("=" * 70)
