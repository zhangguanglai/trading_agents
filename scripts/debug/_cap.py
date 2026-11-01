"""捕获 300058 完整核心洞察输出"""
import sys, io
sys.path.insert(0, '.')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import asyncio
from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer

async def main():
    a = ChipDeepAnalyzer("300058.SZ", 30)
    r = await a.analyze()
    d6 = r.dim6_score
    print(f"=== D4 cost_rise ===")
    print(f"  score={d6.cost_rise.score}, label={d6.cost_rise.label}")
    print(f"  detail: {d4_detail(d6.cost_rise.detail)}")
    print(f"\n=== 全部洞察 ===")
    for i, ins in enumerate(r.core_insights):
        print(f"\n[{i+1}] {ins.title}  (level={ins.level})")
        print(f"    {ins.content}")
    print(f"\n=== 综合 ===")
    print(f"  total={r.dim6_total}/5.5, rating={r.rating}*")

def d4_detail(d):
    """Decode escape for display"""
    return d.encode().decode('unicode_escape') if d else d

asyncio.run(main())
