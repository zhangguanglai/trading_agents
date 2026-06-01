"""chip-deep 核心分析引擎"""

import asyncio
from datetime import datetime, timedelta
from typing import List, Optional

import pandas as pd

from tradingagents.dataflows.interface import route_to_vendor
from .cache import get_cached, set_cached
from .models import (
    ChipDeepResult,
    ChipDistributionItem,
    MarginChangeItem,
    Dim6Score,
    Dim6ScoreItem,
)


class ChipDeepAnalyzer:
    """A股筹码深度分析器
    
    基于 Tushare Pro 的 cyq_perf / cyq_chips 数据，
    计算六维评分并生成分析报告。
    """

    def __init__(self, symbol: str, lookback_days: int = 250):
        self.symbol = symbol
        self.lookback_days = lookback_days
        self.end_date = datetime.now().strftime("%Y-%m-%d")
        self.start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    async def analyze(self) -> ChipDeepResult:
        """执行完整分析流程"""
        # 1. 获取筹码性能指标 (cyq_perf)
        perf_df = await self._get_cyq_perf()
        if perf_df is None or perf_df.empty:
            return self._build_error_result("筹码性能数据获取失败")

        # 2. 获取最新日期的筹码分布 (cyq_chips)
        latest_date = perf_df["trade_date"].max()
        chips_df = await self._get_cyq_chips(latest_date)
        if chips_df is None or chips_df.empty:
            return self._build_error_result("筹码分布数据获取失败")

        # 3. 获取2周前的筹码分布（边际变化）
        prev_date = self._get_prev_trade_date(perf_df, latest_date, days=14)
        prev_chips_df = await self._get_cyq_chips(prev_date) if prev_date else None

        # 4. 获取最新收盘价 (daily 接口)
        close_price = await self._get_close_price(latest_date)

        # 5. 计算六维评分
        dim6 = self._calc_dim6(perf_df, chips_df, prev_chips_df, close_price)

        # 6. 构建结果
        return self._build_result(perf_df, chips_df, prev_chips_df, dim6, close_price)

    async def _get_cyq_perf(self) -> Optional[pd.DataFrame]:
        """获取筹码性能指标（带缓存）"""
        # 尝试缓存
        cached = get_cached(self.symbol, f"{self.start_date}_{self.end_date}", "cyq_perf")
        if cached is not None:
            return cached

        try:
            result = route_to_vendor(
                "get_cyq_perf",
                symbol=self.symbol,
                start_date=self.start_date,
                end_date=self.end_date,
            )
            if result is None:
                return None
            df = result if isinstance(result, pd.DataFrame) else None
            if df is not None:
                set_cached(self.symbol, f"{self.start_date}_{self.end_date}", "cyq_perf", df)
            return df
        except Exception:
            return None

    async def _get_cyq_chips(self, trade_date: str) -> Optional[pd.DataFrame]:
        """获取指定日期的筹码分布（带缓存）"""
        # 尝试缓存
        cached = get_cached(self.symbol, trade_date, "cyq_chips")
        if cached is not None:
            return cached

        try:
            result = route_to_vendor(
                "get_cyq_chips",
                symbol=self.symbol,
                trade_date=trade_date,
            )
            if result is None:
                return None
            df = result if isinstance(result, pd.DataFrame) else None
            if df is not None:
                set_cached(self.symbol, trade_date, "cyq_chips", df)
            return df
        except Exception:
            return None

    async def _get_close_price(self, trade_date: str) -> float:
        """获取指定日期的收盘价（直接使用 Tushare daily 接口）"""
        try:
            # 直接调用 Tushare provider 获取日线数据
            from tradingagents.dataflows.providers.cn_tushare_provider import CnTushareProvider
            provider = CnTushareProvider()
            # trade_date 是 YYYYMMDD 格式，daily 接口需要 YYYY-MM-DD
            if len(trade_date) == 8 and trade_date.isdigit():
                formatted_date = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
            else:
                formatted_date = trade_date
            result = provider.get_stock_data(self.symbol, formatted_date, formatted_date)
            print(f"[chip-deep] tushare daily result for {self.symbol} on {formatted_date}: {result}")
            if result is None:
                return 0
            import json
            if isinstance(result, str):
                try:
                    data = json.loads(result)
                    print(f"[chip-deep] parsed JSON data: {data}")
                    if isinstance(data, list) and len(data) > 0:
                        return float(data[0].get("close", 0))
                    elif isinstance(data, dict) and "error" not in data and "close" in data:
                        return float(data.get("close", 0))
                except json.JSONDecodeError:
                    print(f"[chip-deep] result is not JSON: {result}")
                    return 0
            elif isinstance(result, pd.DataFrame) and not result.empty:
                return float(result.iloc[0].get("close", 0))
        except Exception as e:
            print(f"[chip-deep] get_close_price error: {e}")
        return 0

    def _get_prev_trade_date(self, perf_df: pd.DataFrame, latest_date: str, days: int = 14) -> Optional[str]:
        """从 perf_df 中获取 N 天前的交易日"""
        try:
            dates = sorted(perf_df["trade_date"].unique())
            if len(dates) < 2:
                return None
            # 找到 latest_date 的索引
            idx = dates.index(latest_date)
            target_idx = max(0, idx - days)
            return dates[target_idx]
        except (ValueError, IndexError):
            return None

    def _calc_dim6(self, perf_df: pd.DataFrame, chips_df: pd.DataFrame, prev_chips_df: Optional[pd.DataFrame], close_price: float = 0) -> dict:
        """六维评分计算

        ① 筹码密度: 当前价±10%区间筹码占比 > 50% → ✅
        ② 边际变化: 2周内某区间筹码增加 > 10% → ✅
        ③ 获利盘: < 30% → ✅ (偏冷，底部特征)
        ④ 成本抬升: 均成本较N日前上升 > 20% → ⚠️ (追高型)
        ⑤ 超跌程度: 当前价 < 均成本 → ✅ (偏冷)
        ⑥ 下方支撑: 当前价下方10%有 > 15%筹码 → ✅
        """
        latest = perf_df.iloc[-1]
        # 优先使用 daily 接口获取的收盘价
        close = close_price if close_price > 0 else float(latest.get("weight_avg", 0))
        # 使用筹码峰位成本作为"平均成本"，而不是 weight_avg（历史加权平均会被低价筹码拉低）
        peak_cost = self._calc_peak_cost(chips_df) if chips_df is not None and not chips_df.empty else float(latest.get("weight_avg", 0))
        weight_avg = peak_cost  # 使用峰位成本替代 weight_avg
        winner_rate = float(latest.get("winner_rate", 0))  # 已经是百分比

        # ① 筹码密度
        density = self._calc_chip_density(chips_df, close)
        density_score = 1 if density > 50 else 0
        density_detail = f"当前价±10%区间筹码占比 {density:.1f}%" + ("，筹码集中 ✅" if density_score else "，筹码分散 ❌")

        # ② 边际变化
        margin = self._calc_margin_change(chips_df, prev_chips_df)
        margin_score = 1 if margin > 10 else 0
        margin_detail = f"2周内最大区间筹码增加 {margin:.1f}个百分点" + ("，有承接 ✅" if margin_score else "，变化平缓")

        # ③ 获利盘
        winner_score = 1 if winner_rate < 30 else 0
        winner_detail = f"获利盘 {winner_rate:.1f}%" + ("，偏冷 ✅" if winner_score else "，偏热")

        # ④ 成本抬升
        cost_rise = self._calc_cost_rise(perf_df)
        cost_rise_score = 0 if cost_rise > 20 else 1
        cost_rise_detail = f"均成本较30日前上升 {cost_rise:.1f}%" + ("，追高型 ⚠️" if not cost_rise_score else "，正常 ✅")

        # ⑤ 超跌程度
        overshoot = ((close - weight_avg) / weight_avg * 100) if weight_avg else 0
        overshoot_score = 1 if overshoot < 0 else 0
        overshoot_detail = f"当前价 {close:.2f} vs 均成本 {weight_avg:.2f} ({overshoot:+.1f}%)" + ("，偏冷 ✅" if overshoot_score else "，偏热")

        # ⑥ 下方支撑
        support = self._calc_support_level(chips_df, close)
        support_score = 1 if support > 15 else 0
        support_detail = f"当前价下方10%有 {support:.1f}% 筹码" + ("，有缓冲 ✅" if support_score else "，支撑弱")

        total = density_score + margin_score + winner_score + cost_rise_score + overshoot_score + support_score

        return {
            "chip_density": {"score": density_score, "label": "✅" if density_score else "❌", "detail": density_detail},
            "margin_change": {"score": margin_score, "label": "✅" if margin_score else "❌", "detail": margin_detail},
            "winner_position": {"score": winner_score, "label": "✅" if winner_score else "❌", "detail": winner_detail},
            "cost_rise": {"score": cost_rise_score, "label": "✅" if cost_rise_score else "⚠️", "detail": cost_rise_detail},
            "overshoot": {"score": overshoot_score, "label": "✅" if overshoot_score else "❌", "detail": overshoot_detail},
            "support_level": {"score": support_score, "label": "✅" if support_score else "❌", "detail": support_detail},
            "total": total,
        }

    def _calc_peak_cost(self, chips_df: pd.DataFrame) -> float:
        """计算筹码峰位成本（筹码最集中的价格区间的中点）"""
        if chips_df is None or chips_df.empty:
            return 0
        # 找到筹码占比最高的价格区间（按 2 元区间聚合）
        chips_df = chips_df.sort_values("price").reset_index(drop=True)
        # 使用滑动窗口找到筹码最集中的区间
        best_center = 0
        best_density = 0
        for i, row in chips_df.iterrows():
            price = row["price"]
            # 计算 ±1 元区间的筹码占比
            mask = (chips_df["price"] >= price - 1) & (chips_df["price"] <= price + 1)
            density = chips_df.loc[mask, "percent"].sum()
            if density > best_density:
                best_density = density
                # 使用该区间的加权平均价格作为峰位成本
                subset = chips_df.loc[mask]
                best_center = (subset["price"] * subset["percent"]).sum() / subset["percent"].sum() if subset["percent"].sum() > 0 else price
        return best_center

    def _calc_chip_density(self, chips_df: pd.DataFrame, close: float) -> float:
        """计算当前价±10%区间的筹码占比"""
        if chips_df is None or chips_df.empty:
            return 0
        low, high = close * 0.9, close * 1.1
        mask = (chips_df["price"] >= low) & (chips_df["price"] <= high)
        return chips_df.loc[mask, "percent"].sum() if mask.any() else 0

    def _calc_margin_change(self, chips_df: pd.DataFrame, prev_chips_df: Optional[pd.DataFrame]) -> float:
        """计算2周边际变化最大增幅"""
        if prev_chips_df is None or prev_chips_df.empty or chips_df is None or chips_df.empty:
            return 0
        # 简化：按价格区间聚合后比较
        max_change = 0
        for _, row in chips_df.iterrows():
            price = row.get("price", 0)
            curr_pct = row.get("percent", 0)
            # 找到前期相近价格的筹码占比
            prev_rows = prev_chips_df[abs(prev_chips_df["price"] - price) < 0.5]
            if not prev_rows.empty:
                prev_pct = prev_rows["percent"].iloc[0]
                change = curr_pct - prev_pct
                max_change = max(max_change, change)
        return max_change

    def _calc_cost_rise(self, perf_df: pd.DataFrame) -> float:
        """计算均成本较30日前的上升幅度"""
        if len(perf_df) < 30:
            return 0
        curr_avg = float(perf_df.iloc[-1].get("weight_avg", 0))
        prev_avg = float(perf_df.iloc[-30].get("weight_avg", 0))
        if prev_avg == 0:
            return 0
        return (curr_avg - prev_avg) / prev_avg * 100

    def _calc_support_level(self, chips_df: pd.DataFrame, close: float) -> float:
        """计算当前价下方10%的筹码占比"""
        if chips_df is None or chips_df.empty:
            return 0
        support_price = close * 0.9
        mask = chips_df["price"] <= support_price
        return chips_df.loc[mask, "percent"].sum() if mask.any() else 0

    def _build_result(self, perf_df: pd.DataFrame, chips_df: pd.DataFrame, prev_chips_df: Optional[pd.DataFrame], dim6: dict, close_price: float = 0) -> ChipDeepResult:
        """构建完整分析结果"""
        latest = perf_df.iloc[-1]
        weight_avg = float(latest.get("weight_avg", 0))
        close = close_price if close_price > 0 else weight_avg
        winner_rate = float(latest.get("winner_rate", 0))

        # 筹码分布数据格式化
        chip_dist = []
        if chips_df is not None and not chips_df.empty:
            # 按价格区间分箱聚合
            chips_df = chips_df.sort_values("price")
            bins = pd.cut(chips_df["price"], bins=10)
            grouped = chips_df.groupby(bins)["percent"].sum()
            for interval, pct in grouped.items():
                if pct > 0:
                    chip_dist.append(ChipDistributionItem(
                        price_low=round(interval.left, 2),
                        price_high=round(interval.right, 2),
                        percent=round(pct, 2),
                    ))

        # 边际变化数据
        margin_change = []
        if prev_chips_df is not None and not prev_chips_df.empty and chips_df is not None and not chips_df.empty:
            # 简化为几个关键区间的变化
            for _, row in chips_df.iterrows():
                price = row.get("price", 0)
                curr_pct = row.get("percent", 0)
                prev_rows = prev_chips_df[abs(prev_chips_df["price"] - price) < 0.5]
                if not prev_rows.empty:
                    prev_pct = prev_rows["percent"].iloc[0]
                    change = curr_pct - prev_pct
                    if abs(change) > 1:  # 只记录显著变化
                        margin_change.append(MarginChangeItem(
                            price_low=round(price - 0.5, 2),
                            price_high=round(price + 0.5, 2),
                            prev_pct=round(prev_pct, 2),
                            curr_pct=round(curr_pct, 2),
                            change=round(change, 2),
                        ))
            # 按变化幅度排序，取前5
            margin_change = sorted(margin_change, key=lambda x: abs(x.change), reverse=True)[:5]

        # 评级计算
        rating = min(5, max(1, dim6["total"] + 1))

        # 总结文字
        summary = self._generate_summary(close, weight_avg, winner_rate, dim6)

        return ChipDeepResult(
            meta={
                "symbol": self.symbol,
                "analysis_date": datetime.now().strftime("%Y-%m-%d"),
                "data_date": str(latest.get("trade_date", "")),
                "lookback_days": self.lookback_days,
            },
            current={
                "close": close,
                "weight_avg": weight_avg,
                "cost_5pct": float(latest.get("cost_5pct", 0)),
                "cost_50pct": float(latest.get("cost_50pct", 0)),
                "cost_95pct": float(latest.get("cost_95pct", 0)),
                "winner_rate": winner_rate,
            },
            chip_distribution=chip_dist,
            margin_change_2w=margin_change,
            dim6_score=Dim6Score(
                chip_density=Dim6ScoreItem(**dim6["chip_density"]),
                margin_change=Dim6ScoreItem(**dim6["margin_change"]),
                winner_position=Dim6ScoreItem(**dim6["winner_position"]),
                cost_rise=Dim6ScoreItem(**dim6["cost_rise"]),
                overshoot=Dim6ScoreItem(**dim6["overshoot"]),
                support_level=Dim6ScoreItem(**dim6["support_level"]),
            ),
            dim6_total=dim6["total"],
            rating=rating,
            summary_text=summary,
        )

    def _generate_summary(self, close: float, weight_avg: float, winner_rate: float, dim6: dict) -> str:
        """生成分析总结（参考范例格式）"""
        total = dim6["total"]
        rating = min(5, max(1, total + 1))
        stars = "⭐" * rating

        # 当前价 vs 平均成本
        price_diff = ((close - weight_avg) / weight_avg * 100) if weight_avg else 0
        price_status = "高于" if price_diff > 0 else "低于"

        # 获利盘描述
        if winner_rate < 20:
            winner_desc = "偏冷"
        elif winner_rate < 50:
            winner_desc = "温和"
        else:
            winner_desc = "偏热"

        # 边际变化描述
        margin_has_score = dim6["margin_change"]["score"]
        margin_desc = "有资金在主动买入" if margin_has_score else "筹码变化平缓"

        # 底部特征
        bottom_signals = []
        if dim6["winner_position"]["score"]:
            bottom_signals.append("获利盘偏冷")
        if dim6["overshoot"]["score"]:
            bottom_signals.append("当前价低于平均成本")
        if dim6["support_level"]["score"]:
            bottom_signals.append("下方有筹码支撑")

        bottom_text = "，".join(bottom_signals) if bottom_signals else "底部特征不明显"

        summary = f"""当前价 {close:.2f} {price_status}平均成本 {weight_avg:.2f}（{price_diff:+.1f}%），获利盘 {winner_rate:.1f}% {winner_desc}。{margin_desc}。六维评分 {total}/6，{bottom_text}。评级 {stars}。"""

        return summary

    def _build_error_result(self, reason: str) -> ChipDeepResult:
        """构建错误结果"""
        return ChipDeepResult(
            meta={"symbol": self.symbol, "error": reason},
            current={},
            chip_distribution=[],
            margin_change_2w=[],
            dim6_score=Dim6Score(
                chip_density=Dim6ScoreItem(score=0, label="❌", detail="数据不可用"),
                margin_change=Dim6ScoreItem(score=0, label="❌", detail="数据不可用"),
                winner_position=Dim6ScoreItem(score=0, label="❌", detail="数据不可用"),
                cost_rise=Dim6ScoreItem(score=0, label="❌", detail="数据不可用"),
                overshoot=Dim6ScoreItem(score=0, label="❌", detail="数据不可用"),
                support_level=Dim6ScoreItem(score=0, label="❌", detail="数据不可用"),
            ),
            dim6_total=0,
            rating=1,
            summary_text=f"分析失败：{reason}。请检查股票代码是否正确，或稍后重试。",
        )
