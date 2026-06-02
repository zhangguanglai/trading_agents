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
    PriceStage,
    CoreInsight,
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
        # 0. 获取股票名称
        stock_name = await self._get_stock_name()

        # 1. 获取筹码性能指标 (cyq_perf)
        perf_df = await self._get_cyq_perf()
        if perf_df is None or perf_df.empty:
            return self._build_error_result("筹码性能数据获取失败", stock_name)

        # 确保数据按日期正序排列（旧→新），所有后续方法都依赖这个顺序
        perf_df = perf_df.sort_values("trade_date").reset_index(drop=True)

        # 2. 获取最新日期的筹码分布 (cyq_chips)
        latest_date = perf_df["trade_date"].max()
        chips_df = await self._get_cyq_chips(latest_date)
        if chips_df is None or chips_df.empty:
            return self._build_error_result("筹码分布数据获取失败", stock_name)

        # 3. 获取2周前的筹码分布（边际变化）
        prev_date = self._get_prev_trade_date(perf_df, latest_date, days=14)
        prev_chips_df = await self._get_cyq_chips(prev_date) if prev_date else None

        # 4. 获取最新收盘价 (daily 接口)
        close_price = await self._get_close_price(latest_date)

        # 5. 计算六维评分
        dim6 = self._calc_dim6(perf_df, chips_df, prev_chips_df, close_price)

        # 6. 构建结果
        return self._build_result(perf_df, chips_df, prev_chips_df, dim6, close_price, stock_name)

    async def _get_stock_name(self) -> str:
        """获取股票名称（使用 Tushare stock_basic 接口）"""
        try:
            from tradingagents.dataflows.providers.cn_tushare_provider import CnTushareProvider
            provider = CnTushareProvider()
            provider._init_ts()
            ts_code = provider._to_tushare_code(self.symbol)
            df = provider._call_with_retry(
                provider._ts.stock_basic,
                ts_code=ts_code,
                fields="ts_code,name"
            )
            if df is not None and not df.empty:
                return str(df.iloc[0].get("name", ""))
        except Exception as e:
            print(f"[chip-deep] get_stock_name error: {e}")
        return ""

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
        """六维评分计算（基于量化规则）

        ① 筹码密度: 当前价附近固定区间筹码占比 > 40% → ✅
        ② 边际变化: 当前价附近区间两周增幅 > 10% → ✅
        ③ 获利盘: 20%~60% → ✅，<20%需区分优质/劣质
        ④ 成本抬升: 250日成本抬升 > 15% → ✅，需对比股价涨幅
        ⑤ 超跌程度: ±5%→✅，-10%~-20%→✅机会区，>+15%→❌
        ⑥ 下方支撑: 每5%~7%跌幅内有一道支撑 → ✅
        """
        latest = perf_df.iloc[-1]
        # 优先使用 daily 接口获取的收盘价
        close = close_price if close_price > 0 else float(latest.get("weight_avg", 0))
        # 使用筹码峰位成本作为"平均成本"
        peak_cost = self._calc_peak_cost(chips_df) if chips_df is not None and not chips_df.empty else float(latest.get("weight_avg", 0))
        weight_avg = peak_cost
        winner_rate = float(latest.get("winner_rate", 0))  # 已经是百分比

        # ① 筹码密度（按价格区间计算）
        density, vacuum_risk = self._calc_chip_density_v2(chips_df, close)
        if density > 40:
            density_score = 1
            density_label = "✅"
            density_desc = "厚垫子"
        elif density > 20:
            density_score = 0
            density_label = "⚠️"
            density_desc = "中等支撑"
        else:
            density_score = 0
            density_label = "❌"
            density_desc = "薄支撑"
        density_detail = f"当前价附近筹码占比 {density:.1f}%，{density_desc}"
        if vacuum_risk:
            density_detail += " ⚠️真空悬崖"

        # ② 边际变化（聚焦当前价附近）
        margin, margin_direction = self._calc_margin_change_v2(chips_df, prev_chips_df, close)
        if margin > 10:
            margin_score = 1
            margin_label = "✅"
            margin_desc = "猛烈承接"
        elif margin > 3:
            margin_score = 0
            margin_label = "⚠️"
            margin_desc = "温和承接"
        else:
            margin_score = 0
            margin_label = "❌"
            margin_desc = "无人承接"
        margin_detail = f"当前价附近筹码{margin_direction}{margin:+.1f}个百分点，{margin_desc}"

        # ③ 获利盘（精细化区间）
        # 35%-50%: 黄金区间，主力控盘理想状态
        # 20%-35%: 偏冷，可能是机会区
        # 50%-65%: 偏暖，需警惕
        # <20%: 需区分是主力洗盘还是弱势股
        # 65%-80%: 过热，减仓信号
        # >80%: 极度过热，高风险
        if 35 <= winner_rate <= 50:
            winner_score = 1
            winner_label = "✅"
            winner_desc = "健康均衡（黄金区间）"
        elif 20 <= winner_rate < 35:
            winner_score = 1
            winner_label = "✅"
            winner_desc = "偏冷（机会区）"
        elif 50 < winner_rate <= 65:
            winner_score = 0
            winner_label = "⚠️"
            winner_desc = "偏暖（谨慎）"
        elif winner_rate < 20:
            # 区分优质/劣质低胜率
            is_quality = self._is_quality_low_winner(perf_df, close)
            if is_quality:
                winner_score = 1
                winner_label = "✅"
                winner_desc = "优质低胜率（主力洗盘）"
            else:
                winner_score = 0
                winner_label = "❌"
                winner_desc = "劣质低胜率（弱势股）"
        elif 65 < winner_rate <= 80:
            winner_score = 0
            winner_label = "⚠️"
            winner_desc = "过热（减仓信号）"
        else:  # > 80%
            winner_score = 0
            winner_label = "❌"
            winner_desc = "极度过热（高风险）"
        winner_detail = f"获利盘 {winner_rate:.1f}%，{winner_desc}"

        # ④ 成本抬升（250日数据，对比股价涨幅）
        cost_rise, price_rise, cost_rise_type = self._calc_cost_rise_v2(perf_df, close)
        if cost_rise > 30:
            cost_rise_score = 1
            cost_rise_label = "✅✅"
            cost_rise_desc = "底部系统性大幅抬高"
        elif cost_rise > 15:
            cost_rise_score = 1
            cost_rise_label = "✅"
            cost_rise_desc = "底部明显上移"
        elif cost_rise > 5:
            cost_rise_score = 0
            cost_rise_label = "⚠️"
            cost_rise_desc = "底部部分抬高"
        else:
            cost_rise_score = 0
            cost_rise_label = "❌"
            cost_rise_desc = "底部基本没变"
        cost_rise_detail = f"成本抬升{cost_rise:.1f}%，股价涨幅{price_rise:.1f}%，{cost_rise_type}，{cost_rise_desc}"

        # ⑤ 价格偏离程度（区分超跌和超买）
        # 负值 = 当前价低于成本（超跌），正值 = 当前价高于成本（超买）
        overshoot = ((close - weight_avg) / weight_avg * 100) if weight_avg else 0
        if -5 <= overshoot <= 5:
            # 价格在成本附近 ±5%，合理区间
            overshoot_score = 1
            overshoot_label = "✅"
            overshoot_desc = "价格合理"
        elif -15 <= overshoot < -5:
            # 轻度到中度超跌，机会区
            overshoot_score = 1
            overshoot_label = "✅"
            overshoot_desc = "超跌机会区"
        elif overshoot < -15:
            # 深度超跌，反弹概率高
            overshoot_score = 1
            overshoot_label = "✅"
            overshoot_desc = "深度超跌（反弹概率高）"
        elif 5 < overshoot <= 10:
            # 轻度偏高
            overshoot_score = 0
            overshoot_label = "⚠️"
            overshoot_desc = "轻度偏高"
        elif 10 < overshoot <= 20:
            # 明显偏高，追高风险
            overshoot_score = 0
            overshoot_label = "⚠️"
            overshoot_desc = "明显偏高（追高风险）"
        else:  # > 20%
            # 严重超买，强烈卖出信号
            overshoot_score = 0
            overshoot_label = "❌"
            overshoot_desc = "严重超买（强烈卖出信号）"
        overshoot_detail = f"当前价 {close:.2f} vs 均成本 {weight_avg:.2f} ({overshoot:+.1f}%)，{overshoot_desc}"

        # ⑥ 下方支撑（多层支撑判断）
        support_levels = self._calc_support_levels_v2(chips_df, close)
        if len(support_levels) >= 3:
            support_score = 1
            support_label = "✅"
            support_desc = "层级缓冲良好"
        elif len(support_levels) >= 1:
            support_score = 0
            support_label = "⚠️"
            support_desc = "支撑较薄"
        else:
            support_score = 0
            support_label = "❌"
            support_desc = "真空悬崖"
        support_detail = f"{len(support_levels)}层支撑，{support_desc}"

        total = density_score + margin_score + winner_score + cost_rise_score + overshoot_score + support_score

        return {
            "chip_density": {"score": density_score, "label": density_label, "detail": density_detail},
            "margin_change": {"score": margin_score, "label": margin_label, "detail": margin_detail},
            "winner_position": {"score": winner_score, "label": winner_label, "detail": winner_detail},
            "cost_rise": {"score": cost_rise_score, "label": cost_rise_label, "detail": cost_rise_detail},
            "overshoot": {"score": overshoot_score, "label": overshoot_label, "detail": overshoot_detail},
            "support_level": {"score": support_score, "label": support_label, "detail": support_detail},
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

    def _calc_chip_density_v2(self, chips_df: pd.DataFrame, close: float) -> tuple[float, bool]:
        """计算当前价附近固定区间的筹码占比（基于量化规则）
        
        Returns:
            (density, vacuum_risk): 筹码占比和真空悬崖风险
        """
        if chips_df is None or chips_df.empty:
            return 0, False
        
        # 根据价格确定区间范围
        if close < 20:
            step = 1.5  # 低价股 ±1.5元
        elif close < 100:
            step = 3.5  # 中高价股 ±3~4元
        else:
            step = 5.0  # 高价股 ±5元
        
        # 计算当前价附近区间筹码占比
        low, high = close - step, close + step
        mask = (chips_df["price"] >= low) & (chips_df["price"] <= high)
        density = chips_df.loc[mask, "percent"].sum() if mask.any() else 0
        
        # 真空悬崖判断：当前价下方1元内筹码 < 5%
        below_1_mask = (chips_df["price"] >= close - 1) & (chips_df["price"] < close)
        below_1 = chips_df.loc[below_1_mask, "percent"].sum() if below_1_mask.any() else 0
        vacuum_risk = below_1 < 5
        
        return density, vacuum_risk

    def _calc_margin_change_v2(self, chips_df: pd.DataFrame, prev_chips_df: Optional[pd.DataFrame], close: float) -> tuple[float, str]:
        """计算当前价附近区间的边际变化（基于量化规则）
        
        Returns:
            (margin_change, direction): 变化百分点和方向描述
        """
        if prev_chips_df is None or prev_chips_df.empty or chips_df is None or chips_df.empty:
            return 0, ""
        
        # 确定当前价附近区间
        if close < 20:
            step = 1.5
        elif close < 100:
            step = 3.5
        else:
            step = 5.0
        
        # 计算当前价附近区间的筹码变化
        low, high = close - step, close + step
        curr_mask = (chips_df["price"] >= low) & (chips_df["price"] <= high)
        prev_mask = (prev_chips_df["price"] >= low) & (prev_chips_df["price"] <= high)
        
        curr_pct = chips_df.loc[curr_mask, "percent"].sum() if curr_mask.any() else 0
        prev_pct = prev_chips_df.loc[prev_mask, "percent"].sum() if prev_mask.any() else 0
        
        margin_change = curr_pct - prev_pct
        
        # 方向判断（使用筹码加权平均价格）
        if margin_change > 0:
            prev_weighted_avg = (prev_chips_df["price"] * prev_chips_df["percent"]).sum() / prev_chips_df["percent"].sum() if prev_chips_df["percent"].sum() > 0 else 0
            if close >= prev_weighted_avg:
                direction = "向上集中"
            else:
                direction = "向下承接"
        else:
            direction = "减少"
        
        return margin_change, direction

    def _is_quality_low_winner(self, perf_df: pd.DataFrame, close: float) -> bool:
        """判断是否为优质低胜率（基于量化规则）"""
        if len(perf_df) < 2:
            return False
        
        # 年度涨幅 > 30%
        start_price = float(perf_df.iloc[0].get("weight_avg", 0))
        if start_price > 0:
            annual_return = (close - start_price) / start_price * 100
        else:
            annual_return = 0
        
        # 成本抬升幅度 > 15%
        cost_rise = self._calc_cost_rise_v2(perf_df, close)[0]
        
        return annual_return > 30 and cost_rise > 15

    def _calc_cost_rise_v2(self, perf_df: pd.DataFrame, close: float) -> tuple[float, float, str]:
        """计算250日成本抬升并对比股价涨幅（基于量化规则）
        
        Returns:
            (cost_rise, price_rise, type_desc): 成本抬升、股价涨幅、类型描述
        """
        if len(perf_df) < 2:
            return 0, 0, "数据不足"
        
        # 按日期排序确保正确的时间顺序
        df_sorted = perf_df.sort_values("trade_date")
        
        # 期初和期末数据
        start_avg = float(df_sorted.iloc[0].get("weight_avg", 0))
        end_avg = float(df_sorted.iloc[-1].get("weight_avg", 0))
        
        # 成本抬升幅度
        if start_avg > 0:
            cost_rise = (end_avg - start_avg) / start_avg * 100
        else:
            cost_rise = 0
        
        # 股价涨幅（使用当前收盘价 vs 期初weight_avg作为近似）
        if start_avg > 0:
            price_rise = (close - start_avg) / start_avg * 100
        else:
            price_rise = 0
        
        # 判断类型
        # cost_rise > price_rise: 主力成本抬升快于股价 → 主力在吸筹
        # cost_rise < price_rise: 股价涨速快于成本 → 散户追高
        # abs(diff) <= 10: 两者同步 → 健康换手
        diff = cost_rise - price_rise
        if abs(diff) <= 10:
            type_desc = "健康换手型"
        elif cost_rise > price_rise:
            type_desc = "底部抬升型"
        else:
            type_desc = "追高套牢型"
        
        return cost_rise, price_rise, type_desc

    def _calc_support_levels_v2(self, chips_df: pd.DataFrame, close: float) -> List[dict]:
        """计算多层支撑位（基于量化规则）
        
        支撑位定义：从当前价向下，筹码密集的价格区间。
        这些位置有较多持仓者，下跌时可能产生承接。
        
        Returns:
            List[dict]: 支撑层级列表，每个包含价格和跌幅
        """
        if chips_df is None or chips_df.empty:
            return []
        
        # 筛选当前价下方的筹码，按价格降序排列（从高到低）
        df_below = chips_df[chips_df["price"] <= close].sort_values("price", ascending=False)
        if df_below.empty:
            return []
        
        # 从当前价向下累计筹码
        cum = df_below["percent"].cumsum()
        
        support_levels = []
        for pct_target in [5, 10, 15, 20]:
            mask = cum >= pct_target
            if mask.any():
                # 取最后一个满足条件的价格（离当前价最近的支撑位）
                support_price = df_below[mask]["price"].iloc[-1]
                drop_pct = (1 - support_price / close) * 100
                support_levels.append({
                    "pct": pct_target,
                    "price": support_price,
                    "drop": drop_pct
                })
        
        return support_levels

    # 旧方法已废弃，保留以下方法用于兼容性
    def _calc_support_level(self, chips_df: pd.DataFrame, close: float) -> float:
        """计算当前价下方10%的筹码占比（兼容旧版本）"""
        if chips_df is None or chips_df.empty:
            return 0
        support_price = close * 0.9
        mask = chips_df["price"] <= support_price
        return chips_df.loc[mask, "percent"].sum() if mask.any() else 0

    def _apply_veto_rules(self, dim6: dict) -> int:
        """应用否决项规则（基于量化规则）
        
        以下情况无论其他维度如何，评级不得超过 ⭐⭐：
        - 维度一（筹码密度）判定为 ❌
        - 维度二（边际变化）判定为 ❌ 且方向为"恐慌出逃"
        - 维度四（成本抬升）判定为 ❌
        """
        # 检查否决项
        if dim6["chip_density"]["label"] == "❌":
            return 2  # 最高2星
        
        if dim6["margin_change"]["label"] == "❌" and "恐慌出逃" in dim6["margin_change"]["detail"]:
            return 2
        
        if dim6["cost_rise"]["label"] == "❌":
            return 2
        
        return 5  # 无否决项，正常评级

    def _build_result(self, perf_df: pd.DataFrame, chips_df: pd.DataFrame, prev_chips_df: Optional[pd.DataFrame], dim6: dict, close_price: float = 0, stock_name: str = "") -> ChipDeepResult:
        """构建完整分析结果"""
        latest = perf_df.iloc[-1]
        # 使用筹码峰位成本作为平均成本，而不是 weight_avg（历史加权平均会被低价筹码拉低）
        peak_cost = self._calc_peak_cost(chips_df) if chips_df is not None and not chips_df.empty else float(latest.get("weight_avg", 0))
        weight_avg = peak_cost
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

        # 评级计算（应用否决项规则）
        max_rating = self._apply_veto_rules(dim6)
        base_rating = min(5, max(1, dim6["total"] + 1))
        rating = min(base_rating, max_rating)

        # 总结文字
        summary = self._generate_summary(close, weight_avg, winner_rate, dim6)
        
        # 详细总结（参考范例格式）
        detailed_summary = self._generate_detailed_summary(close, weight_avg, winner_rate, dim6, perf_df, chips_df, margin_change)
        
        # 核心洞察
        core_insights = self._generate_core_insights(close, weight_avg, winner_rate, dim6, perf_df, chips_df, margin_change)
        
        # 价格阶段
        price_stages = self._calc_price_stages(perf_df)

        return ChipDeepResult(
            meta={
                "symbol": self.symbol,
                "name": stock_name,
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
            price_stages=price_stages,
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
            detailed_summary=detailed_summary,
            core_insights=core_insights,
        )

    def _format_date(self, date_str: str) -> str:
        """将日期格式统一转换为 YYYY-MM-DD
        
        支持输入格式:
        - YYYYMMDD (如 20250602)
        - YYYY-MM-DD (如 2025-06-02)
        - YYYY/MM/DD (如 2025/06/02)
        """
        if not date_str or date_str == "nan":
            return ""
        
        date_str = str(date_str).strip()
        
        # 已经是 YYYY-MM-DD 格式
        if len(date_str) == 10 and date_str[4] == '-' and date_str[7] == '-':
            return date_str
        
        # YYYY/MM/DD 格式
        if len(date_str) == 10 and date_str[4] == '/' and date_str[7] == '/':
            return date_str.replace('/', '-')
        
        # YYYYMMDD 格式
        if len(date_str) == 8 and date_str.isdigit():
            return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
        
        return date_str

    def _calc_price_stages(self, perf_df: pd.DataFrame) -> List[PriceStage]:
        """计算价格走势阶段（大涨、回调等）
        
        注意：perf_df 已由 analyze() 方法按 trade_date 正序排列（旧→新）
        """
        if len(perf_df) < 30:
            return []
        
        stages = []
        
        # 使用 weight_avg 作为价格（cyq_perf 没有 close 列）
        price_col = "close" if "close" in perf_df.columns else "weight_avg"
        
        # 找到最高点的索引（在正序数据中）
        max_idx = perf_df[price_col].idxmax()
        
        # 阶段1：从起点到最高点（大涨）
        start_price = float(perf_df.iloc[0].get(price_col, 0))
        max_price = float(perf_df.loc[max_idx].get(price_col, 0))
        max_date = self._format_date(str(perf_df.loc[max_idx].get("trade_date", "")))
        start_date = self._format_date(str(perf_df.iloc[0].get("trade_date", "")))
        
        # 确保最高点不在最开始（至少有30天涨幅才认为是"大涨"阶段）
        if max_idx >= 30 and max_price > start_price * 1.2:  # 涨幅超过20%
            stages.append(PriceStage(
                name="大涨",
                start_date=start_date,
                end_date=max_date,
                start_price=round(start_price, 2),
                end_price=round(max_price, 2),
                change_pct=round((max_price - start_price) / start_price * 100, 1),
                winner_rate_start=float(perf_df.iloc[0].get("winner_rate", 0)),
                winner_rate_end=float(perf_df.loc[max_idx].get("winner_rate", 0)),
            ))
        
        # 阶段2：从最高点到最新（回调）
        latest = perf_df.iloc[-1]
        latest_price = float(latest.get(price_col, 0))
        latest_date = self._format_date(str(latest.get("trade_date", "")))
        
        # 确保最新日期在最高点之后，且回调超过10%
        if max_idx < len(perf_df) - 1 and latest_price < max_price * 0.9:
            stages.append(PriceStage(
                name="深度回调",
                start_date=max_date,
                end_date=latest_date,
                start_price=round(max_price, 2),
                end_price=round(latest_price, 2),
                change_pct=round((latest_price - max_price) / max_price * 100, 1),
                winner_rate_start=float(perf_df.loc[max_idx].get("winner_rate", 0)),
                winner_rate_end=float(latest.get("winner_rate", 0)),
            ))
        
        return stages

    def _generate_summary(self, close: float, weight_avg: float, winner_rate: float, dim6: dict) -> str:
        """生成分析总结（参考范例格式）"""
        total = dim6["total"]
        # 应用与 _build_result 相同的否决规则，确保一致性
        max_rating = self._apply_veto_rules(dim6)
        base_rating = min(5, max(1, total + 1))
        rating = min(base_rating, max_rating)
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

    def _generate_core_insights(self, close: float, weight_avg: float, winner_rate: float, dim6: dict, perf_df: pd.DataFrame, chips_df: pd.DataFrame, margin_change: List[MarginChangeItem]) -> List[CoreInsight]:
        """生成核心洞察列表"""
        insights = []
        price_diff = ((close - weight_avg) / weight_avg * 100) if weight_avg else 0
        
        # 1. 主力意图研判
        density_score = dim6["chip_density"]["score"]
        margin_score = dim6["margin_change"]["score"]
        winner_score = dim6["winner_position"]["score"]
        cost_rise_score = dim6["cost_rise"]["score"]
        
        # 主力吸筹核心条件：筹码集中 + 获利盘低/合理 + 成本抬升
        # 边际变化是辅助确认信号，非必要条件
        if density_score and winner_score and cost_rise_score:
            if margin_score:
                insights.append(CoreInsight(
                    title="主力吸筹信号强烈",
                    content=f"当前价 {close:.2f} 低于平均成本 {weight_avg:.2f}（{price_diff:+.1f}%），获利盘 {winner_rate:.1f}%。筹码集中、成本持续抬升且边际变化积极，表明主力资金正在积极吸筹，后续上涨概率较大。",
                    level="success"
                ))
            else:
                insights.append(CoreInsight(
                    title="主力吸筹迹象（待确认）",
                    content=f"当前价 {close:.2f} 低于平均成本 {weight_avg:.2f}（{price_diff:+.1f}%），获利盘 {winner_rate:.1f}%。筹码集中且成本抬升，但边际变化尚不明显，可能是吸筹初期，建议持续观察。",
                    level="info"
                ))
        elif not density_score and not margin_score:
            insights.append(CoreInsight(
                title="筹码分散，观望为主",
                content=f"当前筹码密度不足，边际变化平缓，缺乏资金主动介入迹象。建议等待筹码重新集中后再考虑布局。",
                level="warning"
            ))
        elif winner_rate > 80:
            insights.append(CoreInsight(
                title="获利盘过高，注意回调风险",
                content=f"获利盘高达 {winner_rate:.1f}%，多数投资者处于盈利状态，抛压可能随时出现。建议逢高减仓，锁定利润。",
                level="danger"
            ))
        
        # 2. 关键价位提示
        if chips_df is not None and not chips_df.empty:
            # 主要成本区
            chips_sorted = chips_df.sort_values("percent", ascending=False)
            top_zone = chips_sorted.iloc[0]
            peak_price = top_zone["price"]
            peak_pct = top_zone["percent"]
            
            if abs(close - peak_price) / peak_price < 0.05:
                insights.append(CoreInsight(
                    title="当前价接近主力成本区",
                    content=f"当前价 {close:.2f} 与主力主要成本区 {peak_price:.2f} 非常接近（偏差 < 5%），此位置支撑较强，是较为安全的买入区域。",
                    level="success"
                ))
            elif close > peak_price * 1.15:
                insights.append(CoreInsight(
                    title="当前价远离主力成本区",
                    content=f"当前价 {close:.2f} 已大幅高于主力成本区 {peak_price:.2f}（+{(close/peak_price-1)*100:.0f}%），追高风险较大，建议等待回调至成本区附近再介入。",
                    level="warning"
                ))
        
        # 3. 周期定位
        stages = self._calc_price_stages(perf_df)
        if stages:
            latest_stage = stages[-1]
            if latest_stage.name == "深度回调":
                insights.append(CoreInsight(
                    title=f"处于深度回调阶段（已回调 {abs(latest_stage.change_pct):.1f}%）",
                    content=f"从最高点 {latest_stage.start_price} 回调至 {latest_stage.end_price}，获利盘从 {latest_stage.winner_rate_start:.1f}% 降至 {latest_stage.winner_rate_end:.1f}%。若六维评分良好，此阶段可能是中长期布局的良机。",
                    level="info"
                ))
            elif latest_stage.name == "大涨":
                insights.append(CoreInsight(
                    title=f"处于上涨阶段（已上涨 {latest_stage.change_pct:.1f}%）",
                    content=f"从 {latest_stage.start_price} 上涨至 {latest_stage.end_price}，获利盘从 {latest_stage.winner_rate_start:.1f}% 升至 {latest_stage.winner_rate_end:.1f}%。注意获利盘过高后的回调风险。",
                    level="info"
                ))
        
        # 4. 操作策略
        total = dim6["total"]
        if total >= 5:
            insights.append(CoreInsight(
                title="六维评分优秀，积极看多",
                content=f"六维评分 {total}/6，多项指标共振向好。建议在回调时分批建仓，止损位设在主要成本区下方 5-7%。",
                level="success"
            ))
        elif total >= 3:
            insights.append(CoreInsight(
                title="六维评分中等，谨慎参与",
                content=f"六维评分 {total}/6，部分指标向好但存在分歧。建议小仓位试探，等待信号进一步明确后再加仓。",
                level="info"
            ))
        else:
            insights.append(CoreInsight(
                title="六维评分偏弱，建议观望",
                content=f"六维评分 {total}/6，多数指标未达标。当前不是最佳介入时机，建议耐心等待筹码结构改善。",
                level="warning"
            ))
        
        # 5. 风险预警
        if not dim6["support_level"]["score"]:
            insights.append(CoreInsight(
                title="下方支撑薄弱，注意下跌风险",
                content="当前价下方筹码稀疏，形成'真空悬崖'。一旦跌破关键支撑位，可能引发连锁抛售，下跌空间较大。",
                level="danger"
            ))
        
        if dim6["cost_rise"]["label"] == "❌":
            insights.append(CoreInsight(
                title="成本未抬升，缺乏上涨动能",
                content="250日成本基本未变，说明长期资金并未积极进场。缺乏成本抬升支撑，股价上涨持续性存疑。",
                level="warning"
            ))
        
        return insights

    def _generate_detailed_summary(self, close: float, weight_avg: float, winner_rate: float, dim6: dict, perf_df: pd.DataFrame, chips_df: pd.DataFrame, margin_change: List[MarginChangeItem]) -> str:
        """生成详细分析总结（参考范例格式）"""
        total = dim6["total"]
        # 应用与 _build_result 相同的否决规则，确保一致性
        max_rating = self._apply_veto_rules(dim6)
        base_rating = min(5, max(1, total + 1))
        rating = min(base_rating, max_rating)
        stars = "⭐" * rating

        # 预计算价格状态
        price_diff = ((close - weight_avg) / weight_avg * 100) if weight_avg else 0
        price_status = "高于" if price_diff > 0 else "低于"

        # 价格走势
        stages = self._calc_price_stages(perf_df)
        price_trend = ""
        if stages:
            for stage in stages:
                price_trend += f"• {stage.name}：{stage.start_date} ~ {stage.end_date}，{stage.start_price} → {stage.end_price}（{stage.change_pct:+.1f}%），获利盘 {stage.winner_rate_start:.1f}% → {stage.winner_rate_end:.1f}%\n"
        else:
            price_trend = "• 暂无显著趋势阶段\n"

        # 最新价格与成本
        latest = perf_df.iloc[-1]
        latest_date = self._format_date(str(latest.get("trade_date", "")))
        cost_5pct = float(latest.get("cost_5pct", 0))
        cost_50pct = float(latest.get("cost_50pct", 0))
        cost_95pct = float(latest.get("cost_95pct", 0))

        # 筹码结构描述（更详细）
        chip_structure = ""
        if chips_df is not None and not chips_df.empty:
            # 按价格区间分箱聚合，展示当前价附近的筹码结构
            chips_sorted = chips_df.sort_values("price")
            # 使用与 _build_result 相同的分箱逻辑（10个区间）
            bins = pd.cut(chips_sorted["price"], bins=10)
            grouped = chips_sorted.groupby(bins)["percent"].sum().sort_values(ascending=False)
            chip_zones = []
            for i, (interval, pct) in enumerate(grouped.head(5).items(), 1):
                if pct > 0:
                    chip_zones.append(f"  {i}. [{interval.left:.1f}, {interval.right:.1f}) 占比 {pct:.2f}%")
            chip_structure = "\n".join(chip_zones) if chip_zones else "  筹码分布较为分散"
        else:
            chip_structure = "  数据不可用"

        # 筹码集中度
        if cost_5pct > 0 and cost_95pct > 0:
            concentration = cost_95pct - cost_5pct
            if concentration < 20:
                conc_desc = "高度集中"
            elif concentration < 40:
                conc_desc = "中度集中"
            else:
                conc_desc = "分散"
            chip_concentration = f"5%成本位: {cost_5pct:.2f} | 50%成本位: {cost_50pct:.2f} | 95%成本位: {cost_95pct:.2f}\n筹码集中度(95%-5%): {concentration:.2f}（{conc_desc}）"
        else:
            chip_concentration = "筹码集中度数据不可用"

        # 边际变化描述
        margin_desc = ""
        if margin_change:
            top_changes = sorted(margin_change, key=lambda x: abs(x.change), reverse=True)[:5]
            changes = []
            for item in top_changes:
                direction = "↑" if item.change > 0 else "↓"
                changes.append(f"  [{item.price_low:.1f}, {item.price_high:.1f}) {item.prev_pct:.1f}% → {item.curr_pct:.1f}% ({direction}{abs(item.change):.1f}%)")
            margin_desc = "\n".join(changes)
        else:
            margin_desc = "  变化平缓或数据不足"

        # 六维判定（更详细）
        judgments = []
        dim_names = {
            "chip_density": "① 筹码密度",
            "margin_change": "② 边际变化",
            "winner_position": "③ 获利盘",
            "cost_rise": "④ 成本抬升",
            "overshoot": "⑤ 超跌程度",
            "support_level": "⑥ 下方支撑",
        }
        for key, name in dim_names.items():
            item = dim6[key]
            score = item["score"]
            label = item["label"]
            detail = item["detail"]
            status = "通过" if score else "未通过"
            judgments.append(f"  {name} {label} | {status}\n    {detail}")

        judgments_text = "\n\n".join(judgments) if judgments else "  暂无明确信号"

        # 生成详细总结
        detailed = f"""═══════════════════════════════════════
📊 筹码深度分析报告
═══════════════════════════════════════

【一、价格走势总览】
{price_trend}
【二、最新价格与成本】（数据日期: {latest_date}）
  当前收盘价: {close:.2f}
  加权平均成本: {weight_avg:.2f}
  价格偏离成本: {price_diff:+.1f}%（{price_status}成本）
  获利盘比例: {winner_rate:.1f}%

【三、筹码结构分布】
{chip_structure}

【四、筹码集中度】
{chip_concentration}

【五、2周边际变化】
{margin_desc}

【六、六维评分详解】
{judgments_text}

═══════════════════════════════════════
【综合评级】{stars}（{total}/6 分）
═══════════════════════════════════════

【一句话总结】
当前价 {close:.2f} {price_status}平均成本 {weight_avg:.2f}（{price_diff:+.1f}%），获利盘 {winner_rate:.1f}%。六维评分 {total}/6，评级 {stars}。
"""

        return detailed

    def _build_error_result(self, reason: str, stock_name: str = "") -> ChipDeepResult:
        """构建错误结果"""
        return ChipDeepResult(
            meta={"symbol": self.symbol, "name": stock_name, "error": reason},
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
