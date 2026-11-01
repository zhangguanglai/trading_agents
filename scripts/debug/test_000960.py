"""审查锡业股份 000960 数据正确性"""

import asyncio
import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

from tradingagents.chip_deep import ChipDeepAnalyzer

async def review_000960():
    """审查锡业股份 000960"""
    print("=" * 70)
    print("锡业股份 000960 数据审查")
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
        
        # 六维评分详细审查
        print("─" * 70)
        print("【六维评分详细审查】")
        print("─" * 70)
        
        dim6 = result.dim6_score
        
        # 维度1: 筹码密度
        print("\n① 筹码密度:")
        print(f"   评分: {'✅' if dim6.chip_density.score else '❌'} {dim6.chip_density.detail}")
        print(f"   标签: {dim6.chip_density.label}")
        
        # 维度2: 边际变化
        print("\n② 边际变化:")
        print(f"   评分: {'✅' if dim6.margin_change.score else '❌'} {dim6.margin_change.detail}")
        print(f"   标签: {dim6.margin_change.label}")
        
        # 维度3: 获利盘
        print("\n③ 获利盘:")
        print(f"   评分: {'✅' if dim6.winner_position.score else '❌'} {dim6.winner_position.detail}")
        print(f"   标签: {dim6.winner_position.label}")
        
        # 维度4: 成本抬升
        print("\n④ 成本抬升:")
        print(f"   评分: {'✅' if dim6.cost_rise.score else '❌'} {dim6.cost_rise.detail}")
        print(f"   标签: {dim6.cost_rise.label}")
        
        # 维度5: 超跌程度
        print("\n⑤ 超跌程度:")
        print(f"   评分: {'✅' if dim6.overshoot.score else '❌'} {dim6.overshoot.detail}")
        print(f"   标签: {dim6.overshoot.label}")
        
        # 维度6: 下方支撑
        print("\n⑥ 下方支撑:")
        print(f"   评分: {'✅' if dim6.support_level.score else '❌'} {dim6.support_level.detail}")
        print(f"   标签: {dim6.support_level.label}")
        
        print()
        
        # 综合评级
        print("─" * 70)
        print("【综合评级】")
        print("─" * 70)
        print(f"六维总分: {result.dim6_total}/6")
        print(f"综合评级: {'⭐' * result.rating} ({result.rating}星)")
        print(f"总结: {result.summary_text}")
        print()
        
        # 数据一致性检查
        print("─" * 70)
        print("【数据一致性检查】")
        print("─" * 70)
        
        # 检查1: 当前价 vs 平均成本
        close = result.current.get('close', 0)
        weight_avg = result.current.get('weight_avg', 0)
        if close > 0 and weight_avg > 0:
            deviation = (close - weight_avg) / weight_avg * 100
            print(f"✓ 价格偏离: {deviation:.1f}% (当前价 {close} vs 平均成本 {weight_avg})")
            
            # 检查超跌程度描述是否匹配
            if deviation > 10:
                expected = "明显偏高"
            elif deviation > 5:
                expected = "轻度偏高"
            elif deviation > -5:
                expected = "价格合理"
            elif deviation > -15:
                expected = "超跌机会区"
            else:
                expected = "深度超跌"
            
            if expected in dim6.overshoot.detail:
                print(f"✓ 超跌程度描述正确: {expected}")
            else:
                print(f"✗ 超跌程度描述可能不匹配: 期望 '{expected}', 实际 '{dim6.overshoot.detail}'")
        
        # 检查2: 获利盘与价格关系
        winner_rate = result.current.get('winner_rate', 0)
        if winner_rate > 80:
            print(f"✗ 获利盘过高: {winner_rate}% (>80% 极度过热)")
        elif winner_rate > 65:
            print(f"⚠ 获利盘偏热: {winner_rate}% (65%-80% 过热)")
        elif winner_rate >= 35:
            print(f"✓ 获利盘合理: {winner_rate}% (35%-65% 健康)")
        else:
            print(f"✓ 获利盘偏低: {winner_rate}% (<35% 偏冷)")
        
        # 检查3: 成本抬升与股价涨幅
        if "成本抬升" in dim6.cost_rise.detail:
            # 提取数值
            import re
            cost_rise_match = re.search(r'成本抬升([\d.]+)%', dim6.cost_rise.detail)
            price_rise_match = re.search(r'股价涨幅([\d.]+)%', dim6.cost_rise.detail)
            
            if cost_rise_match and price_rise_match:
                cost_rise = float(cost_rise_match.group(1))
                price_rise = float(price_rise_match.group(1))
                
                if cost_rise > price_rise:
                    expected_type = "底部抬升型"
                elif abs(cost_rise - price_rise) <= 10:
                    expected_type = "健康换手型"
                else:
                    expected_type = "追高套牢型"
                
                if expected_type in dim6.cost_rise.detail:
                    print(f"✓ 成本抬升类型正确: {expected_type}")
                else:
                    print(f"✗ 成本抬升类型可能不匹配: 期望 '{expected_type}'")
                    print(f"  成本抬升: {cost_rise}%, 股价涨幅: {price_rise}%")
        
        print()
        
        # 输出详细报告
        print("─" * 70)
        print("【详细报告】")
        print("─" * 70)
        print(result.detailed_summary)
        print()
        
        print("=" * 70)
        print("审查完成！")
        print("=" * 70)
        
    except Exception as e:
        print(f"❌ 分析过程中出现错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(review_000960())
