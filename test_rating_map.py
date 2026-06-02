"""验证评分与评级映射关系"""

import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer

# 创建模拟的 analyzer 实例
analyzer = ChipDeepAnalyzer("000001.SZ")

print("=" * 70)
print("评分与评级映射关系验证")
print("=" * 70)
print()

# 测试各分档
test_cases = [
    {"total": 6, "desc": "完美"},
    {"total": 5, "desc": "优秀"},
    {"total": 4, "desc": "良好"},
    {"total": 3, "desc": "一般"},
    {"total": 2, "desc": "较弱"},
    {"total": 1, "desc": "差"},
    {"total": 0, "desc": "极差"},
]

print("【基础映射（无否决项）】")
print("-" * 70)
for case in test_cases:
    total = case["total"]
    rating = analyzer._calc_base_rating(total)
    stars = "⭐" * rating
    print(f"  {total}/6 → {stars} ({rating}星) | {case['desc']}")

print()
print("【否决项规则】")
print("-" * 70)
print("  当以下任一条件满足时，评级最高限制为 ⭐⭐（2星）：")
print("  • 筹码密度 ❌")
print("  • 边际变化 ❌ 且 恐慌出逃")
print("  • 成本抬升 ❌")

print()
print("【综合示例】")
print("-" * 70)

# 模拟不同场景
scenarios = [
    {"name": "完美股票", "total": 6, "veto": False},
    {"name": "优秀股票", "total": 5, "veto": False},
    {"name": "良好股票", "total": 4, "veto": False},
    {"name": "一般股票", "total": 3, "veto": False},
    {"name": "较弱股票", "total": 2, "veto": False},
    {"name": "差股票", "total": 1, "veto": False},
    {"name": "良好但筹码分散", "total": 4, "veto": True},
    {"name": "优秀但成本未抬升", "total": 5, "veto": True},
]

for s in scenarios:
    base = analyzer._calc_base_rating(s["total"])
    max_r = 2 if s["veto"] else 5
    final = min(base, max_r)
    veto_text = " [否决]" if s["veto"] else ""
    print(f"  {s['name']}: {s['total']}/6 → 基础{base}星 → 最终{'⭐' * final} ({final}星){veto_text}")

print()
print("=" * 70)
print("验证完成！")
print("=" * 70)
