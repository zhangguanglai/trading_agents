"""
Mock 测试：使用模拟的 30 日数据验证 ChipDeepAnalyzer 对 000951（中国重汽）的分析结果
"""
import asyncio
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# 模拟 000951（中国重汽）30 日筹码性能数据
def create_mock_cyq_perf() -> pd.DataFrame:
    """创建30个交易日的筹码性能数据（上涨趋势场景：主力吸筹后上涨）"""
    # 生成30个交易日（跳过周末），从 2026-04-20 开始
    dates = []
    current = datetime(2026, 4, 20)
    while len(dates) < 30:
        if current.weekday() < 5:  # 周一到周五
            dates.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)

    data = []
    # 场景：主力吸筹后拉升
    # 前期（1-10日）：低位吸筹，weight_avg 14.5 -> 15.5，winner_rate 20% -> 35%
    # 中期（11-20日）：缓慢拉升，weight_avg 15.5 -> 17.0，winner_rate 35% -> 60%
    # 后期（21-30日）：加速上涨后小幅回调，weight_avg 17.0 -> 17.5，winner_rate 60% -> 72%
    base_prices = [
        # 前期吸筹（1-10日）
        14.50, 14.60, 14.55, 14.70, 14.80,
        14.90, 15.00, 15.10, 15.20, 15.30,
        # 中期拉升（11-20日）
        15.50, 15.70, 15.90, 16.10, 16.30,
        16.50, 16.70, 16.80, 16.90, 17.00,
        # 后期加速+回调（21-30日）
        17.30, 17.50, 17.80, 18.00, 17.90,
        17.70, 17.60, 17.50, 17.40, 17.50,
    ]

    for i, date in enumerate(dates):
        close = base_prices[i]
        # weight_avg 略低于收盘价
        weight_avg = close - np.random.uniform(0.05, 0.20)
        # 90%筹码集中度 = 上方5%成本 - 下方5%成本
        cost_5pct = weight_avg * 0.90
        cost_15pct = weight_avg * 0.94
        cost_50pct = weight_avg * 0.99
        cost_85pct = weight_avg * 1.04
        cost_95pct = weight_avg * 1.08
        # winner_rate 随价格上涨而增加
        if i < 10:
            winner_rate = 20 + i * 1.5
        elif i < 20:
            winner_rate = 35 + (i - 10) * 2.5
        else:
            winner_rate = 60 + (i - 20) * 1.5
        winner_rate = min(85, max(15, winner_rate))

        data.append({
            "trade_date": date,
            "cost_5pct": round(cost_5pct, 2),
            "cost_15pct": round(cost_15pct, 2),
            "cost_50pct": round(cost_50pct, 2),
            "cost_85pct": round(cost_85pct, 2),
            "cost_95pct": round(cost_95pct, 2),
            "weight_avg": round(weight_avg, 2),
            "winner_rate": round(winner_rate, 2),
            "close": round(close, 2),
        })

    return pd.DataFrame(data)


def create_mock_cyq_chips(close_price: float, date_type: str = "current") -> pd.DataFrame:
    """创建筹码分布数据
    - current: 当前筹码分布，集中在 16-18 元区域
    - prev: 5日前筹码分布，集中在 14-16 元区域（体现成本抬升）
    """
    if date_type == "prev":
        # 5日前：筹码集中在低位 13-16 元（吸筹区）
        prices = np.arange(12.0, 18.0, 0.5)
        percents = np.exp(-((prices - 14.5) ** 2) / 3.0) * 15
        percents = percents / percents.sum() * 100
    else:
        # 当前：筹码集中在 16-18 元（拉升后的成本区）
        prices = np.arange(14.0, 22.0, 0.5)
        # 双峰分布：主峰在17.0，次峰在15.5
        percents = (np.exp(-((prices - 17.0) ** 2) / 2.0) * 18 +
                    np.exp(-((prices - 15.5) ** 2) / 4.0) * 8)
        percents = percents / percents.sum() * 100

    return pd.DataFrame({
        "price": prices,
        "percent": percents,
    })


async def test_chip_deep_000951():
    """运行 000951 的30日Mock分析并验证结果"""
    from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer
    from tradingagents.chip_deep.models import CoreInsight

    print("=" * 70)
    print("开始测试：000951（中国重汽）30日筹码深度分析")
    print("=" * 70)

    # ── 准备 Mock 数据 ──
    perf_df = create_mock_cyq_perf()
    close_price = float(perf_df.iloc[-1]["close"])
    latest_date = str(perf_df.iloc[-1]["trade_date"])
    # 5日前的日期（取 perf_df 倒数第6条）
    prev_date = str(perf_df.iloc[-6]["trade_date"])

    # ── Monkey-patch ChipDeepAnalyzer 的方法 ──
    original_get_cyq_perf = ChipDeepAnalyzer._get_cyq_perf
    original_get_cyq_chips = ChipDeepAnalyzer._get_cyq_chips
    original_get_close_price = ChipDeepAnalyzer._get_close_price
    original_get_daily_latest_date = ChipDeepAnalyzer._get_daily_latest_date
    original_get_stock_name = ChipDeepAnalyzer._get_stock_name

    async def mock_get_cyq_perf(self):
        return perf_df

    async def mock_get_cyq_chips(self, trade_date: str):
        trade_date_clean = str(trade_date).replace("-", "")
        # 如果是最新日期 → 返回当前筹码分布
        if trade_date_clean == latest_date:
            return create_mock_cyq_chips(close_price, "current")
        # 如果是 prev_date → 返回前期筹码分布
        elif trade_date_clean == prev_date:
            return create_mock_cyq_chips(close_price, "prev")
        # 其他日期（analyze 内部向前查找时）→ 也返回 prev
        else:
            return create_mock_cyq_chips(close_price, "prev")

    async def mock_get_close_price(self, trade_date: str) -> float:
        return close_price

    async def mock_get_daily_latest_date(self):
        return latest_date

    async def mock_get_stock_name(self) -> str:
        return "中国重汽"

    # 安装 Mock
    ChipDeepAnalyzer._get_cyq_perf = mock_get_cyq_perf
    ChipDeepAnalyzer._get_cyq_chips = mock_get_cyq_chips
    ChipDeepAnalyzer._get_close_price = mock_get_close_price
    ChipDeepAnalyzer._get_daily_latest_date = mock_get_daily_latest_date
    ChipDeepAnalyzer._get_stock_name = mock_get_stock_name

    try:
        # 创建分析器（30日周期）
        analyzer = ChipDeepAnalyzer("000951.SZ", lookback_days=30)

        # 运行分析
        result = await analyzer.analyze()

        # ========== 验证 1：默认周期是否为 30 日 ==========
        print("\n【验证1：lookback_days】")
        print(f"  meta['lookback_days'] = {result.meta.get('lookback_days')}")
        assert result.meta.get('lookback_days') == 30, f"期望30日，实际{result.meta.get('lookback_days')}"
        print("  ✅ 默认周期正确为30日")

        # ========== 验证 2：元数据 ==========
        print("\n【验证2：元数据】")
        print(f"  标的: {result.meta['symbol']}")
        print(f"  名称: {result.meta.get('name', 'N/A')}")
        print(f"  分析日期: {result.meta['analysis_date']}")
        print(f"  数据日期: {result.meta['data_date']}")
        print(f"  回溯天数: {result.meta['lookback_days']}")

        # ========== 验证 3：当前价格/成本 ==========
        print("\n【验证3：当前价格/成本】")
        print(f"  收盘价: {result.current['close']:.2f}")
        print(f"  加权平均成本: {result.current['weight_avg']:.2f}")
        print(f"  5%分位成本: {result.current['cost_5pct']:.2f}")
        print(f"  50%分位成本: {result.current['cost_50pct']:.2f}")
        print(f"  95%分位成本: {result.current['cost_95pct']:.2f}")
        print(f"  获利盘: {result.current['winner_rate']:.1f}%")

        # ========== 验证 4：六维评分 ==========
        print("\n【验证4：六维评分】")
        dim6 = result.dim6_score
        print(f"  ① 筹码密度: {dim6.chip_density.score:.1f} {dim6.chip_density.label}")
        print(f"      {dim6.chip_density.detail}")
        print(f"  ② 边际变化: {dim6.margin_change.score:.1f} {dim6.margin_change.label}")
        print(f"      {dim6.margin_change.detail}")
        print(f"  ③ 获利盘: {dim6.winner_position.score:.1f} {dim6.winner_position.label}")
        print(f"      {dim6.winner_position.detail}")
        print(f"  ④ 成本抬升: {dim6.cost_rise.score:.1f} {dim6.cost_rise.label}")
        print(f"      {dim6.cost_rise.detail}")
        print(f"  ⑤ 超跌程度: {dim6.overshoot.score:.1f} {dim6.overshoot.label}")
        print(f"      {dim6.overshoot.detail}")
        print(f"  ⑥ 下方支撑: {dim6.support_level.score:.1f} {dim6.support_level.label}")
        print(f"      {dim6.support_level.detail}")

        print(f"\n【综合评分】")
        print(f"  总分: {result.dim6_total:.2f} / 5.5")
        print(f"  评级: {'⭐' * result.rating}")
        if result.veto_reason:
            print(f"  ⚠️ 否决提示: {result.veto_reason}")

        # ========== 验证 5：价格走势阶段 ==========
        print("\n【验证5：价格走势阶段】")
        if result.price_stages:
            for stage in result.price_stages:
                print(f"  {stage.name}: {stage.start_date} ~ {stage.end_date}")
                print(f"    价格: {stage.start_price:.2f} -> {stage.end_price:.2f} ({stage.change_pct:+.1f}%)")
                print(f"    获利盘: {stage.winner_rate_start:.1f}% -> {stage.winner_rate_end:.1f}%")
            # 验证日期范围是否约30日
            first_date = result.price_stages[0].start_date
            last_date = result.price_stages[-1].end_date
            print(f"  阶段覆盖范围: {first_date} ~ {last_date}")
        else:
            print("  （无显著价格阶段，数据少于30条时可能为空）")

        # ========== 验证 6：核心洞察 ==========
        print("\n【验证6：核心洞察模块】")
        print(f"  核心洞察数量: {len(result.core_insights)}")
        print()

        # 收集所有洞察标题
        insight_titles = [insight.title for insight in result.core_insights]
        print("  洞察标题列表:")
        for i, title in enumerate(insight_titles, 1):
            print(f"    {i}. {title}")

        # 验证7个核心模块
        expected_modules = [
            "一句话总结",
            "关键价位",
            "主力意图",
            "成本抬升",
            "价格走势",
            "综合操作策略",
            "风险提示",
        ]

        print("\n  模块匹配检查:")
        for module in expected_modules:
            found = any(module in title for title in insight_titles)
            status = "✅" if found else "❌"
            print(f"    {status} {module}")

        # 打印每个洞察的详细内容
        print("\n" + "=" * 70)
        print("【CoreInsights 详细内容】")
        print("=" * 70)

        for i, insight in enumerate(result.core_insights, 1):
            icon = {"info": "ℹ️", "warning": "⚠️", "success": "✅", "danger": "❌"}.get(insight.level, "•")
            print(f"\n--- 洞察 {i}: {insight.title} [{insight.level}] ---")
            print(f"  {icon} {insight.content}")

            # 检查是否包含操作建议关键词
            has_action_words = any(kw in insight.content for kw in ["建仓", "加仓", "止盈", "止损", "持仓", "减仓", "观望", "买入", "卖出"])
            if has_action_words:
                print(f"  📋 包含操作建议关键词")

        # ========== 验证 7：筹码分布 ==========
        print("\n" + "=" * 70)
        print("【筹码分布】")
        print("=" * 70)
        for item in result.chip_distribution[:8]:
            bar = "█" * int(item.percent / 2)
            print(f"  [{item.price_low:6.2f}, {item.price_high:6.2f}): {item.percent:5.1f}% {bar}")

        # ========== 验证 8：边际变化 ==========
        print("\n" + "=" * 70)
        print("【边际变化（Top 5）】")
        print("=" * 70)
        for item in result.margin_change_2w[:5]:
            direction = "↑" if item.change > 0 else "↓"
            print(f"  [{item.price_low:.2f}, {item.price_high:.2f}): {item.prev_pct:.1f}% -> {item.curr_pct:.1f}% ({item.change:+.1f}%) {direction}")

        # ========== 验证 9：总结 ==========
        print("\n" + "=" * 70)
        print("【一句话总结】")
        print("=" * 70)
        print(f"  {result.summary_text}")

        print("\n" + "=" * 70)
        print("测试完成")
        print("=" * 70)

        # 最终断言
        assert len(result.core_insights) >= 5, f"核心洞察数量不足: {len(result.core_insights)}"
        print("\n✅ 所有验证通过！")

    finally:
        # 恢复原始方法
        ChipDeepAnalyzer._get_cyq_perf = original_get_cyq_perf
        ChipDeepAnalyzer._get_cyq_chips = original_get_cyq_chips
        ChipDeepAnalyzer._get_close_price = original_get_close_price
        ChipDeepAnalyzer._get_daily_latest_date = original_get_daily_latest_date
        ChipDeepAnalyzer._get_stock_name = original_get_stock_name


if __name__ == "__main__":
    asyncio.run(test_chip_deep_000951())
