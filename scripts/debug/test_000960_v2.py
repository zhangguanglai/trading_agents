"""验证锡业股份 000960 修复后的数据一致性"""

import asyncio
import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

from tradingagents.chip_deep import ChipDeepAnalyzer

async def test_000960_v2():
    """测试锡业股份 000960（修复后）"""
    print("=" * 70)
    print("锡业股份 000960 修复验证")
    print("=" * 70)
    print()
    
    analyzer = ChipDeepAnalyzer("000960.SZ", lookback_days=250)
    
    print("正在获取数据并分析，请稍候...")
    print()
    
    try:
        result = await analyzer.analyze()
        
        if result.meta.get("error"):
            print(f"❌ 分析失败: {result.meta['error']}")
            return
        
        # 基本信息
        print("─" * 70)
        print("【基本信息】")
        print("─" * 70)
        print(f"股票名称: {result.meta.get('name', '未知')}")
        print(f"股票代码: {result.meta['symbol']}")
        print(f"数据日期: {result.meta.get('data_date', '未知')}")
        print(f"当前价: {result.current.get('close', '未知')}")
        print(f"平均成本: {result.current.get('weight_avg', '未知')}")
        print(f"获利盘: {result.current.get('winner_rate', '未知')}%")
        print()
        
        # 六维评分
        print("─" * 70)
        print("【六维评分】")
        print("─" * 70)
        dim6 = result.dim6_score
        print(f"① 筹码密度: {'✅' if dim6.chip_density.score else '❌'} {dim6.chip_density.detail}")
        print(f"② 边际变化: {'✅' if dim6.margin_change.score else '❌'} {dim6.margin_change.detail}")
        print(f"③ 获利盘: {'✅' if dim6.winner_position.score else '❌'} {dim6.winner_position.detail}")
        print(f"④ 成本抬升: {'✅' if dim6.cost_rise.score else '❌'} {dim6.cost_rise.detail}")
        print(f"⑤ 超跌程度: {'✅' if dim6.overshoot.score else '❌'} {dim6.overshoot.detail}")
        print(f"⑥ 下方支撑: {'✅' if dim6.support_level.score else '❌'} {dim6.support_level.detail}")
        print()
        
        # 综合评级
        print("─" * 70)
        print("【综合评级】")
        print("─" * 70)
        print(f"六维总分: {result.dim6_total}/6")
        print(f"综合评级: {'⭐' * result.rating} ({result.rating}星)")
        print()
        
        # 数据一致性检查
        print("─" * 70)
        print("【数据一致性检查】")
        print("─" * 70)
        
        close = result.current.get('close', 0)
        weight_avg = result.current.get('weight_avg', 0)
        winner_rate = result.current.get('winner_rate', 0)
        
        # 检查1: 当前价 vs 平均成本
        if close > 0 and weight_avg > 0:
            deviation = (close - weight_avg) / weight_avg * 100
            print(f"✓ 价格偏离: {deviation:.1f}%")
            
            # 验证获利盘是否合理
            if close > weight_avg and winner_rate < 50:
                print(f"⚠️ 当前价高于平均成本，但获利盘仅 {winner_rate}%")
                print(f"  可能原因: 筹码分布数据与当前价不匹配")
            elif close < weight_avg and winner_rate > 70:
                print(f"⚠️ 当前价低于平均成本，但获利盘高达 {winner_rate}%")
                print(f"  可能原因: 筹码分布数据与当前价不匹配")
            else:
                print(f"✓ 获利盘数据合理: {winner_rate}%")
        
        # 检查2: 筹码密度
        if dim6.chip_density.score and dim6.chip_density.label == "✅":
            print(f"✓ 筹码密度通过: {dim6.chip_density.detail}")
        else:
            print(f"✗ 筹码密度未通过: {dim6.chip_density.detail}")
        
        print()
        print("=" * 70)
        print("验证完成！")
        print("=" * 70)
        
    except Exception as e:
        print(f"❌ 分析过程中出现错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_000960_v2())
