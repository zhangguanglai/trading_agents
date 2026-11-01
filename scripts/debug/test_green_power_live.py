"""绿色动力 601330 实际数据验证

直接调用 ChipDeepAnalyzer 进行实际验证，不依赖后端服务。
"""

import asyncio
import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

from tradingagents.chip_deep import ChipDeepAnalyzer

async def test_green_power():
    """测试绿色动力 601330"""
    print("=" * 70)
    print("绿色动力 601330 筹码深度分析 - 实际数据验证")
    print("=" * 70)
    print()
    
    # 创建分析器
    analyzer = ChipDeepAnalyzer("601330.SH", lookback_days=250)
    
    print("正在获取数据并分析，请稍候...")
    print()
    
    try:
        # 执行分析
        result = await analyzer.analyze()
        
        # 检查是否有错误
        if result.meta.get("error"):
            print(f"❌ 分析失败: {result.meta['error']}")
            return
        
        # 输出基本信息
        print("─" * 70)
        print("【基本信息】")
        print("─" * 70)
        print(f"股票代码: {result.meta['symbol']}")
        print(f"股票名称: {result.meta.get('name', 'N/A')}")
        print(f"分析日期: {result.meta['analysis_date']}")
        print(f"数据日期: {result.meta.get('data_date', 'N/A')}")
        print(f"回溯天数: {result.meta.get('lookback_days', 'N/A')}")
        print()
        
        # 输出当前价格信息
        print("─" * 70)
        print("【当前价格信息】")
        print("─" * 70)
        current = result.current
        print(f"当前价: {current.get('close', 'N/A')}")
        print(f"平均成本: {current.get('weight_avg', 'N/A')}")
        print(f"获利盘: {current.get('winner_rate', 'N/A')}%")
        print(f"5%成本位: {current.get('cost_5pct', 'N/A')}")
        print(f"50%成本位: {current.get('cost_50pct', 'N/A')}")
        print(f"95%成本位: {current.get('cost_95pct', 'N/A')}")
        print()
        
        # 计算价格偏离
        close = current.get('close', 0)
        weight_avg = current.get('weight_avg', 0)
        if close and weight_avg:
            overshoot = ((close - weight_avg) / weight_avg * 100)
            print(f"价格偏离: {overshoot:+.1f}%")
            if -5 <= overshoot <= 5:
                print("  → 价格合理区间 ✅")
            elif -15 <= overshoot < -5:
                print("  → 超跌机会区 ✅")
            elif overshoot < -15:
                print("  → 深度超跌 ✅")
            elif 5 < overshoot <= 10:
                print("  → 轻度偏高 ⚠️")
            elif 10 < overshoot <= 20:
                print("  → 明显偏高（追高风险）⚠️")
            else:
                print("  → 严重超买 ❌")
        print()
        
        # 输出六维评分
        print("─" * 70)
        print("【六维评分】")
        print("─" * 70)
        dim6 = result.dim6_score
        dimensions = [
            ("筹码密度", dim6.chip_density),
            ("边际变化", dim6.margin_change),
            ("获利盘", dim6.winner_position),
            ("成本抬升", dim6.cost_rise),
            ("超跌程度", dim6.overshoot),
            ("下方支撑", dim6.support_level),
        ]
        
        for name, item in dimensions:
            status = "✅" if item.score else "❌"
            print(f"  {name}: {item.label} | {item.detail}")
        
        print()
        print(f"总分: {result.dim6_total}/6")
        print(f"评级: {'⭐' * result.rating}")
        print()
        
        # 输出价格走势阶段
        if result.price_stages:
            print("─" * 70)
            print("【价格走势阶段】")
            print("─" * 70)
            for stage in result.price_stages:
                print(f"  {stage.name}:")
                print(f"    时间: {stage.start_date} ~ {stage.end_date}")
                print(f"    价格: {stage.start_price} → {stage.end_price} ({stage.change_pct:+.1f}%)")
                print(f"    获利盘: {stage.winner_rate_start:.1f}% → {stage.winner_rate_end:.1f}%")
                print()
        
        # 输出核心洞察
        if result.core_insights:
            print("─" * 70)
            print("【核心洞察】")
            print("─" * 70)
            for insight in result.core_insights:
                level_icon = {
                    "success": "✅",
                    "warning": "⚠️",
                    "danger": "❌",
                    "info": "ℹ️"
                }.get(insight.level, "•")
                print(f"  {level_icon} {insight.title}")
                print(f"    {insight.content}")
                print()
        
        # 输出总结
        print("─" * 70)
        print("【一句话总结】")
        print("─" * 70)
        print(result.summary_text)
        print()
        
        # 验证关键逻辑
        print("=" * 70)
        print("【关键逻辑验证】")
        print("=" * 70)
        
        # 验证 1: 成本抬升类型判断
        cost_rise_detail = dim6.cost_rise.detail
        if "底部抬升型" in cost_rise_detail:
            print("✅ 成本抬升逻辑正确: 识别为底部抬升型（主力吸筹）")
        elif "追高套牢型" in cost_rise_detail:
            print("✅ 成本抬升逻辑正确: 识别为追高套牢型（散户追高）")
        elif "健康换手型" in cost_rise_detail:
            print("✅ 成本抬升逻辑正确: 识别为健康换手型")
        else:
            print(f"⚠️ 成本抬升类型: {cost_rise_detail}")
        
        # 验证 2: 支撑位计算
        if dim6.support_level.score:
            print("✅ 支撑位逻辑正确: 识别到有下方支撑")
        else:
            print("⚠️ 支撑位逻辑: 下方支撑薄弱")
        
        # 验证 3: 获利盘评分
        winner_detail = dim6.winner_position.detail
        if "黄金区间" in winner_detail or "机会区" in winner_detail or "优质低胜率" in winner_detail:
            print(f"✅ 获利盘评分正确: {winner_detail}")
        else:
            print(f"⚠️ 获利盘评分: {winner_detail}")
        
        # 验证 4: 价格偏离
        if dim6.overshoot.score:
            print(f"✅ 价格偏离逻辑正确: {dim6.overshoot.detail}")
        else:
            print(f"⚠️ 价格偏离: {dim6.overshoot.detail}")
        
        print()
        print("=" * 70)
        print("验证完成！")
        print("=" * 70)
        
    except Exception as e:
        print(f"❌ 分析过程中出现错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_green_power())
