"""
Mock 测试：使用模拟的 30 日 Tushare 数据运行 chip-deep 分析
"""
import asyncio
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# 模拟 Tushare cyq_perf 数据结构
def create_mock_cyq_perf(start_date: str, days: int = 30) -> pd.DataFrame:
    """创建模拟的筹码性能指标数据"""
    dates = []
    base_date = datetime.strptime(start_date, "%Y%m%d")
    
    # 生成30个交易日（跳过周末）
    current = base_date
    while len(dates) < days:
        if current.weekday() < 5:  # 周一到周五
            dates.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    
    data = []
    # 模拟股价从 30 涨到 36，然后回调到 33（与筹码分布范围 25-40 匹配）
    base_price = 30.0
    for i, date in enumerate(dates):
        if i < 20:
            # 上涨阶段
            price = base_price + i * 0.3  # 30 -> 36
        else:
            # 回调阶段
            price = 36.0 - (i - 20) * 0.3  # 36 -> 33
        
        # weight_avg 略低于价格，形成轻度超跌
        weight_avg = price * 0.98 + np.random.normal(0, 0.1)
        
        # winner_rate 随价格上涨而增加，回调时减少
        if i < 20:
            winner_rate = 30 + i * 2.5  # 30% -> 80%
        else:
            winner_rate = 80 - (i - 20) * 1.5  # 80% -> 65%
        
        data.append({
            "trade_date": date,
            "cost_5pct": weight_avg * 0.85,
            "cost_15pct": weight_avg * 0.90,
            "cost_50pct": weight_avg * 0.98,
            "cost_85pct": weight_avg * 1.05,
            "cost_95pct": weight_avg * 1.10,
            "weight_avg": weight_avg,
            "winner_rate": min(95, max(5, winner_rate)),
            "close": price,  # 添加 close 列用于 _calc_period_cost_rise
        })
    
    return pd.DataFrame(data)


def create_mock_cyq_chips(close_price: float, scenario: str = "healthy") -> pd.DataFrame:
    """创建模拟的筹码分布数据"""
    if scenario == "healthy":
        # 健康分布：筹码集中在当前价附近
        prices = np.arange(25, 40, 0.5)
        # 以 close_price 为中心的正态分布
        percents = np.exp(-((prices - close_price) ** 2) / 8) * 15
        percents = percents / percents.sum() * 100
    elif scenario == "dispersed":
        # 分散分布
        prices = np.arange(25, 40, 0.5)
        percents = np.ones_like(prices) * 2
        percents = percents / percents.sum() * 100
    else:
        # 底部集中
        prices = np.arange(25, 40, 0.5)
        percents = np.exp(-((prices - 28) ** 2) / 5) * 20
        percents = percents / percents.sum() * 100
    
    return pd.DataFrame({
        "price": prices,
        "percent": percents,
    })


# Mock 路由函数
class MockDataRouter:
    """模拟数据路由，拦截 Tushare API 调用"""
    
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.perf_df = None
        self.chips_cache = {}
    
    def route(self, method: str, **kwargs):
        if method == "get_cyq_perf":
            if self.perf_df is None:
                start = kwargs.get("start_date", "20250501")
                self.perf_df = create_mock_cyq_perf(start.replace("-", ""))
            return self.perf_df
        
        elif method == "get_cyq_chips":
            trade_date = kwargs.get("trade_date", "")
            if trade_date not in self.chips_cache:
                # 根据日期模拟不同的筹码分布
                if trade_date.endswith("01") or trade_date.endswith("15"):
                    self.chips_cache[trade_date] = create_mock_cyq_chips(33, "healthy")
                else:
                    self.chips_cache[trade_date] = create_mock_cyq_chips(33, "healthy")
            return self.chips_cache[trade_date]
        
        return None


async def test_mock_analysis():
    """使用 Mock 数据运行分析"""
    from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer
    
    # 创建 Mock 路由器
    router = MockDataRouter("000001.SZ")
    
    # 替换路由函数
    import tradingagents.chip_deep.analyzer as analyzer_module
    original_route = analyzer_module.route_to_vendor
    analyzer_module.route_to_vendor = lambda method, **kwargs: router.route(method, **kwargs)
    
    # 同时Mock _get_close_price 和 _get_daily_latest_date 避免真实API调用
    original_get_close = ChipDeepAnalyzer._get_close_price
    original_get_daily_latest = ChipDeepAnalyzer._get_daily_latest_date
    
    async def mock_get_close_price(self, trade_date):
        # 从Mock数据中获取收盘价
        if router.perf_df is not None and not router.perf_df.empty:
            latest = router.perf_df.iloc[-1]
            return float(latest.get("close", latest.get("weight_avg", 33.0)))
        return 33.0
    
    async def mock_get_daily_latest_date(self):
        # 返回Mock数据的最新日期
        if router.perf_df is not None and not router.perf_df.empty:
            return str(router.perf_df.iloc[-1]["trade_date"])
        return None
    
    ChipDeepAnalyzer._get_close_price = mock_get_close_price
    ChipDeepAnalyzer._get_daily_latest_date = mock_get_daily_latest_date
    
    try:
        # 创建分析器（30日周期）
        analyzer = ChipDeepAnalyzer("000001.SZ", lookback_days=30)
        
        # 运行分析
        result = await analyzer.analyze()
        
        print("=" * 60)
        print("Mock 30日筹码深度分析结果")
        print("=" * 60)
        print(f"\n【元数据】")
        print(f"  标的: {result.meta['symbol']} ({result.meta['name']})")
        print(f"  分析日期: {result.meta['analysis_date']}")
        print(f"  数据日期: {result.meta['data_date']}")
        print(f"  回溯天数: {result.meta['lookback_days']}")
        
        print(f"\n【当前价格/成本】")
        print(f"  收盘价: {result.current['close']:.2f}")
        print(f"  加权平均成本: {result.current['weight_avg']:.2f}")
        print(f"  5%分位成本: {result.current['cost_5pct']:.2f}")
        print(f"  50%分位成本: {result.current['cost_50pct']:.2f}")
        print(f"  95%分位成本: {result.current['cost_95pct']:.2f}")
        print(f"  获利盘: {result.current['winner_rate']:.1f}%")
        
        print(f"\n【六维评分】")
        dim6 = result.dim6_score
        print(f"  ① 边际变化: {dim6.margin_change.score:.1f} {dim6.margin_change.label}")
        print(f"      {dim6.margin_change.detail}")
        print(f"  ② 筹码密度: {dim6.chip_density.score:.1f} {dim6.chip_density.label}")
        print(f"      {dim6.chip_density.detail}")
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
            print(f"  ⚠️ 提示: {result.veto_reason}")
        
        print(f"\n【一句话总结】")
        print(f"  {result.summary_text}")
        
        print(f"\n【核心洞察】")
        for insight in result.core_insights:
            icon = {"info": "ℹ️", "warning": "⚠️", "success": "✅", "danger": "❌"}.get(insight.level, "•")
            print(f"  {icon} {insight.title}: {insight.content}")
        
        print(f"\n【价格阶段】")
        for stage in result.price_stages:
            print(f"  {stage.name}: {stage.start_date} ~ {stage.end_date}")
            print(f"    价格: {stage.start_price:.2f} -> {stage.end_price:.2f} ({stage.change_pct:+.1f}%)")
            print(f"    获利盘: {stage.winner_rate_start:.1f}% -> {stage.winner_rate_end:.1f}%")
        
        print(f"\n【筹码分布】")
        for item in result.chip_distribution[:5]:
            print(f"  [{item.price_low:.2f}, {item.price_high:.2f}): {item.percent:.1f}%")
        
        print(f"\n【边际变化】")
        for item in result.margin_change_2w[:5]:
            direction = "↑" if item.change > 0 else "↓"
            print(f"  [{item.price_low:.2f}, {item.price_high:.2f}): {item.prev_pct:.1f}% -> {item.curr_pct:.1f}% ({item.change:+.1f}%) {direction}")
        
        print("\n" + "=" * 60)
        print("分析完成")
        print("=" * 60)
        
    finally:
        # 恢复原始路由和方法
        analyzer_module.route_to_vendor = original_route
        ChipDeepAnalyzer._get_close_price = original_get_close
        ChipDeepAnalyzer._get_daily_latest_date = original_get_daily_latest


if __name__ == "__main__":
    asyncio.run(test_mock_analysis())
