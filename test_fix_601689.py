"""测试 601689 的星级一致性和数据一致性修复"""

import asyncio
import sys
sys.path.insert(0, 'd:\\MyWorkspace\\TradingAgents-AShare')

from tradingagents.chip_deep import ChipDeepAnalyzer

async def test_601689():
    """测试拓普集团 601689"""
    print("=" * 70)
    print("拓普集团 601689 修复验证")
    print("=" * 70)
    print()
    
    analyzer = ChipDeepAnalyzer("601689.SH", lookback_days=250)
    
    print("正在获取数据并分析，请稍候...")
    print()
    
    try:
        result = await analyzer.analyze()
        
        if result.meta.get("error"):
            print(f"❌ 分析失败: {result.meta['error']}")
            return
        
        # 验证 1: 星级一致性
        print("─" * 70)
        print("【验证 1: 星级一致性】")
        print("─" * 70)
        
        # 看板显示的星级
        dashboard_rating = result.rating
        dashboard_stars = "⭐" * dashboard_rating
        
        # 详细报告中的星级（从 summary_text 中提取）
        summary_text = result.summary_text
        detailed_rating = summary_text.count("⭐")
        
        # 详细总结中的星级
        detailed_summary = result.detailed_summary
        detailed_summary_rating = detailed_summary.count("⭐")
        
        print(f"看板星级 (result.rating): {dashboard_stars} ({dashboard_rating}星)")
        print(f"总结文字星级: {'⭐' * detailed_rating} ({detailed_rating}星)")
        print(f"详细报告星级: {'⭐' * detailed_summary_rating} ({detailed_summary_rating}星)")
        
        if dashboard_rating == detailed_rating == detailed_summary_rating:
            print("✅ 星级一致！")
        else:
            print("❌ 星级不一致！")
            print(f"  看板: {dashboard_rating}星")
            print(f"  总结: {detailed_rating}星")
            print(f"  详细: {detailed_summary_rating}星")
        
        print()
        
        # 验证 2: 六维评分与星级关系
        print("─" * 70)
        print("【验证 2: 六维评分与星级关系】")
        print("─" * 70)
        
        dim6 = result.dim6_score
        total = result.dim6_total
        
        print(f"六维总分: {total}/6")
        print(f"各维度评分:")
        print(f"  筹码密度: {'✅' if dim6.chip_density.score else '❌'} {dim6.chip_density.detail}")
        print(f"  边际变化: {'✅' if dim6.margin_change.score else '❌'} {dim6.margin_change.detail}")
        print(f"  获利盘: {'✅' if dim6.winner_position.score else '❌'} {dim6.winner_position.detail}")
        print(f"  成本抬升: {'✅' if dim6.cost_rise.score else '❌'} {dim6.cost_rise.detail}")
        print(f"  超跌程度: {'✅' if dim6.overshoot.score else '❌'} {dim6.overshoot.detail}")
        print(f"  下方支撑: {'✅' if dim6.support_level.score else '❌'} {dim6.support_level.detail}")
        
        # 计算基础评级
        base_rating = min(5, max(1, total + 1))
        print(f"\n基础评级 (总分+1): {base_rating}星")
        
        # 检查否决项
        veto_reasons = []
        if dim6.chip_density.label == "❌":
            veto_reasons.append("筹码密度 ❌")
        if dim6.margin_change.label == "❌" and "恐慌出逃" in dim6.margin_change.detail:
            veto_reasons.append("边际变化 ❌ (恐慌出逃)")
        if dim6.cost_rise.label == "❌":
            veto_reasons.append("成本抬升 ❌")
        
        if veto_reasons:
            print(f"否决项限制: 最高2星")
            print(f"  原因: {'; '.join(veto_reasons)}")
        else:
            print(f"否决项: 无")
        
        print(f"最终评级: {dashboard_rating}星")
        
        if dashboard_rating <= 2 and veto_reasons:
            print("✅ 否决规则生效，评级正确！")
        elif dashboard_rating == base_rating and not veto_reasons:
            print("✅ 评级计算正确！")
        else:
            print("⚠️ 评级可能有误，请检查")
        
        print()
        
        # 验证 3: 筹码分布数据
        print("─" * 70)
        print("【验证 3: 筹码分布数据】")
        print("─" * 70)
        
        close = result.current.get("close", 0)
        weight_avg = result.current.get("weight_avg", 0)
        
        print(f"当前价: {close}")
        print(f"平均成本: {weight_avg}")
        
        # 从详细报告中提取筹码分布
        print("\n详细报告中的筹码分布:")
        if "【三、筹码结构分布】" in detailed_summary:
            chip_section = detailed_summary.split("【三、筹码结构分布】")[1].split("【四、筹码集中度】")[0]
            print(chip_section.strip())
        
        # 检查筹码分布是否合理（当前价是否在分布范围内）
        chip_dist = result.chip_distribution
        if chip_dist:
            prices = [item.price_low for item in chip_dist] + [item.price_high for item in chip_dist]
            min_price = min(prices)
            max_price = max(prices)
            print(f"\n筹码分布价格范围: {min_price:.2f} ~ {max_price:.2f}")
            print(f"当前价 {close} 是否在范围内: {'是' if min_price <= close <= max_price else '否'}")
            
            if min_price <= close <= max_price:
                print("✅ 筹码分布数据合理")
            else:
                print("⚠️ 当前价不在筹码分布范围内，可能需要检查")
        
        print()
        
        # 验证 4: 筹码集中度
        print("─" * 70)
        print("【验证 4: 筹码集中度】")
        print("─" * 70)
        
        if "【四、筹码集中度】" in detailed_summary:
            conc_section = detailed_summary.split("【四、筹码集中度】")[1].split("【五、2周边际变化】")[0]
            print(conc_section.strip())
        
        cost_5pct = result.current.get("cost_5pct", 0)
        cost_50pct = result.current.get("cost_50pct", 0)
        cost_95pct = result.current.get("cost_95pct", 0)
        
        print(f"\n成本位数据:")
        print(f"  5%成本位: {cost_5pct}")
        print(f"  50%成本位: {cost_50pct}")
        print(f"  95%成本位: {cost_95pct}")
        
        if cost_5pct > 0 and cost_95pct > 0:
            concentration = cost_95pct - cost_5pct
            print(f"  集中度(95%-5%): {concentration:.2f}")
            if concentration < 20:
                print("  状态: 高度集中")
            elif concentration < 40:
                print("  状态: 中度集中")
            else:
                print("  状态: 分散")
        
        print()
        print("=" * 70)
        print("验证完成！")
        print("=" * 70)
        
    except Exception as e:
        print(f"❌ 分析过程中出现错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_601689())
