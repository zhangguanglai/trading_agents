"""chip-deep 核心分析引擎"""

import asyncio
from datetime import datetime, timedelta
from typing import List, Optional

import numpy as np
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

        # 2. 获取最新交易日（优先使用 daily 接口的最新日期，确保数据一致性）
        latest_date = perf_df["trade_date"].max()
        
        # 尝试获取 daily 数据的最新日期（可能比 cyq_perf 更新）
        daily_latest_date = await self._get_daily_latest_date()
        if daily_latest_date and daily_latest_date > latest_date:
            latest_date = daily_latest_date
            print(f"[chip-deep] 使用 daily 接口的最新日期: {latest_date}")

        # 3. 获取最新日期的筹码分布 (cyq_chips)
        chips_df = await self._get_cyq_chips(latest_date)
        if chips_df is None or chips_df.empty:
            return self._build_error_result("筹码分布数据获取失败", stock_name)

        # 4. 获取2周前的筹码分布（边际变化）
        prev_date = self._get_prev_trade_date(perf_df, latest_date, days=14)
        prev_chips_df = await self._get_cyq_chips(prev_date) if prev_date else None

        # 5. 获取最新收盘价 (daily 接口)
        close_price = await self._get_close_price(latest_date)

        # 6. 计算六维评分
        dim6 = self._calc_dim6(perf_df, chips_df, prev_chips_df, close_price)

        # 7. 构建结果
        return self._build_result(perf_df, chips_df, prev_chips_df, dim6, close_price, stock_name, latest_date)

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

    async def _get_daily_latest_date(self) -> Optional[str]:
        """获取 daily 接口的最新交易日（YYYYMMDD格式）
        
        注意：Tushare 返回的数据是按日期降序排列（最新在前），
        所以取第一条数据即为最新日期。
        """
        try:
            from tradingagents.dataflows.providers.cn_tushare_provider import CnTushareProvider
            provider = CnTushareProvider()
            # 获取最近一个交易日的数据
            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            result = provider.get_stock_data(self.symbol, start_date, end_date)
            if result is not None:
                import json
                if isinstance(result, str):
                    try:
                        data = json.loads(result)
                        if isinstance(data, list) and len(data) > 0:
                            # Tushare 返回的数据按日期降序排列，第一条即为最新
                            latest_date = data[0].get("date", "")
                            if latest_date:
                                # 转换为 YYYYMMDD 格式
                                return latest_date.replace("-", "")
                    except json.JSONDecodeError:
                        pass
                elif isinstance(result, pd.DataFrame) and not result.empty:
                    # DataFrame 也是按日期降序排列
                    latest_date = result.iloc[0].get("date", "")
                    if latest_date:
                        return str(latest_date).replace("-", "")
        except Exception as e:
            print(f"[chip-deep] get_daily_latest_date error: {e}")
        return None

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

    def _get_bin_params(self, close: float) -> tuple:
        """根据股价自动选择分箱粒度和搜索范围（技能文档标准）
        
        Returns:
            (step, search_range)
        """
        if close < 20:
            return 1.0, 1.5  # 低价股：1元一格，±1.5元搜索范围
        elif close < 100:
            return 2.0, 3.0  # 中价股：2元一格，±3元搜索范围
        else:
            return 5.0, 8.0  # 高价股：5元一格，±8元搜索范围

    def _calc_dim6(self, perf_df: pd.DataFrame, chips_df: pd.DataFrame, prev_chips_df: Optional[pd.DataFrame], close_price: float = 0) -> dict:
        """六维评分计算（基于量化规则）— 技能文档 v2 加权版

        加权评分体系：
        ① 边际变化 ★: 权重最高（基础分 2.0）
        ② 筹码密度: 重要（基础分 1.0）
        ③ 获利盘: 重要（基础分 1.0）
        ④ 成本抬升: 辅助（基础分 0.5）
        ⑤ 超跌程度: 辅助（基础分 0.5）
        ⑥ 下方支撑: 辅助（基础分 0.5）
        总分范围：0 ~ 5.5
        """
        latest = perf_df.iloc[-1]
        # 优先使用 daily 接口获取的收盘价
        close = close_price if close_price > 0 else float(latest.get("weight_avg", 0))
        # 使用筹码峰位成本作为"平均成本"
        peak_cost = self._calc_peak_cost(chips_df) if chips_df is not None and not chips_df.empty else float(latest.get("weight_avg", 0))
        weight_avg = peak_cost
        winner_rate = float(latest.get("winner_rate", 0))  # 已经是百分比

        # 获取分箱参数
        step, search_range = self._get_bin_params(close)

        # ① 边际变化（★ 高权重维度，基础分 2.0）
        margin, margin_direction = self._calc_margin_change_v2(chips_df, prev_chips_df, close)
        # 判断方向：在当前价或更高区间增加 → 多方主动买入 ✅
        # 在低于当前价区间增加 → 被动承接 ⚠️
        # 在高于当前价区间大减 → 恐慌出逃 ❌
        margin_direction_type = "unknown"
        if margin > 10:
            if margin_direction == "向上集中":
                margin_score = 2.0
                margin_label = "✅"
                margin_desc = "猛烈承接（多方主动买入）"
                margin_direction_type = "active_buy"
            else:
                margin_score = 0.5
                margin_label = "⚠️"
                margin_desc = "被动承接（下方增加）"
                margin_direction_type = "passive"
        elif margin > 3:
            if margin_direction == "向上集中":
                margin_score = 0.5
                margin_label = "⚠️"
                margin_desc = "温和承接"
                margin_direction_type = "mild_up"
            else:
                margin_score = 0.0
                margin_label = "❌"
                margin_desc = "无人承接"
                margin_direction_type = "no_support"
        else:
            margin_score = 0.0
            margin_label = "❌"
            margin_desc = "无人承接"
            margin_direction_type = "no_support"
        margin_detail = f"当前价附近筹码{margin_direction}{margin:+.1f}个百分点，{margin_desc}"

        # ② 筹码密度分布（重要维度，基础分 1.0）
        density, vacuum_risk = self._calc_chip_density_v2(chips_df, close)
        # 按技能文档标准：根据股价档位调整阈值
        if close < 20:
            thick_threshold = 40  # 低价股
        elif close < 100:
            thick_threshold = 40  # 中价股
        else:
            thick_threshold = 40  # 高价股
        
        if density > thick_threshold:
            density_score = 1.0
            density_label = "✅"
            density_desc = "厚垫子"
        elif density > 20:
            density_score = 0.5
            density_label = "⚠️"
            density_desc = "中等支撑"
        else:
            density_score = 0.0
            density_label = "❌"
            density_desc = "薄支撑"
        density_detail = f"当前价附近筹码占比 {density:.1f}%，{density_desc}"
        if vacuum_risk:
            density_detail += " ⚠️真空悬崖"

        # ③ 获利盘位置（重要维度，基础分 1.0）
        # 技能文档标准：
        # >90%: 极度过热 ❌⚠️
        # 80%~90%: 过热 ⚠️
        # 60%~80%: 偏暖 ✅
        # 40%~60%: 均衡（最健康）✅
        # 20%~40%: 偏冷 ✅
        # <20%: 需区分优质/劣质
        if winner_rate > 90:
            winner_score = 0.0
            winner_label = "❌⚠️"
            winner_desc = "极度过热（不买入，持有者减仓）"
        elif winner_rate > 80:
            winner_score = 0.0
            winner_label = "⚠️"
            winner_desc = "过热（持有不加仓，设止盈）"
        elif winner_rate > 60:
            winner_score = 1.0
            winner_label = "✅"
            winner_desc = "偏暖（可持有，正常）"
        elif winner_rate > 40:
            winner_score = 1.0
            winner_label = "✅"
            winner_desc = "均衡（最健康，可买入或持有）"
        elif winner_rate > 20:
            winner_score = 1.0
            winner_label = "✅"
            winner_desc = "偏冷（可关注，等边际确认）"
        else:  # < 20%
            # 区分优质/劣质低胜率
            is_quality = self._is_quality_low_winner(perf_df, close)
            if is_quality:
                winner_score = 1.0
                winner_label = "✅"
                winner_desc = "优质低胜率（主力洗盘，可关注抄底）"
            else:
                winner_score = 0.0
                winner_label = "❌"
                winner_desc = "劣质低胜率（弱势股，不碰）"
        winner_detail = f"获利盘 {winner_rate:.1f}%，{winner_desc}"

        # ④ 成本结构抬升（辅助维度，基础分 0.5）
        cost_rise, price_rise, cost_rise_type = self._calc_cost_rise_v2(perf_df, close)
        # 规则 4a：成本抬升幅度
        if cost_rise > 30:
            cost_rise_score = 0.5
            cost_rise_label = "✅✅"
            cost_rise_desc = "底部大幅抬高"
        elif cost_rise > 15:
            cost_rise_score = 0.5
            cost_rise_label = "✅"
            cost_rise_desc = "底部明显上移"
        elif cost_rise > 5:
            cost_rise_score = 0.25
            cost_rise_label = "⚠️"
            cost_rise_desc = "底部部分抬高"
        else:
            cost_rise_score = 0.0
            cost_rise_label = "❌"
            cost_rise_desc = "底部基本没变"
        
        # 规则 4b：成本涨幅 vs 股价涨幅比值
        if price_rise != 0:
            ratio = cost_rise / price_rise
            if 0.9 <= ratio <= 1.1:
                cost_rise_type = "健康换手型"
            elif ratio < 0.9:
                cost_rise_type = "底部抬升型"
            else:
                cost_rise_type = "追高套牢型"
        
        cost_rise_detail = f"成本抬升{cost_rise:.1f}%，股价涨幅{price_rise:.1f}%，{cost_rise_type}，{cost_rise_desc}"

        # ⑤ 超跌程度（辅助维度，基础分 0.5）
        overshoot = ((close - weight_avg) / weight_avg * 100) if weight_avg else 0
        if overshoot > 15:
            overshoot_score = 0.0
            overshoot_label = "❌⚠️"
            overshoot_desc = "显著高于成本（卖出或减仓）"
        elif overshoot > 3:
            overshoot_score = 0.25
            overshoot_label = "⚠️"
            overshoot_desc = "略高于成本（不追高，等回调）"
        elif overshoot >= -5:
            overshoot_score = 0.5
            overshoot_label = "✅"
            overshoot_desc = "正常波动（可买入或持有）"
        elif overshoot >= -10:
            overshoot_score = 0.25
            overshoot_label = "⚠️"
            overshoot_desc = "轻度超跌（关注，不可重仓）"
        elif overshoot >= -20:
            overshoot_score = 0.5
            overshoot_label = "✅"
            overshoot_desc = "中度超跌（可分批建仓，历史机会区）"
        else:  # < -20%
            overshoot_score = 0.5
            overshoot_label = "✅"
            overshoot_desc = "极度超跌（需基本面配合，不可仅凭筹码买入）"
        overshoot_detail = f"当前价 {close:.2f} vs 均成本 {weight_avg:.2f} ({overshoot:+.1f}%)，{overshoot_desc}"

        # ⑥ 下方支撑层级（辅助维度，基础分 0.5）
        support_levels = self._calc_support_levels_v2(chips_df, close)
        if len(support_levels) >= 3:
            support_score = 0.5
            support_label = "✅"
            support_desc = "层级良好（可承受回调，不恐慌）"
        elif len(support_levels) >= 1:
            support_score = 0.25
            support_label = "⚠️"
            support_desc = "偏薄（跌破首层应减仓）"
        else:
            support_score = 0.0
            support_label = "❌"
            support_desc = "真空悬崖（必须设止损，破位加速跌）"
        support_detail = f"{len(support_levels)}层支撑，{support_desc}"

        # 加权总分计算
        total = margin_score + density_score + winner_score + cost_rise_score + overshoot_score + support_score

        return {
            "chip_density": {"score": density_score, "label": density_label, "detail": density_detail},
            "margin_change": {"score": margin_score, "label": margin_label, "detail": margin_detail},
            "winner_position": {"score": winner_score, "label": winner_label, "detail": winner_detail},
            "cost_rise": {"score": cost_rise_score, "label": cost_rise_label, "detail": cost_rise_detail},
            "overshoot": {"score": overshoot_score, "label": overshoot_label, "detail": overshoot_detail},
            "support_level": {"score": support_score, "label": support_label, "detail": support_detail},
            "total": total,
            "margin_direction_type": margin_direction_type,  # 用于核心洞察
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
        
        技能文档标准：
        - 股价 < 20元: 搜索范围 ±1.5元
        - 股价 20~100元: 搜索范围 ±3.0元
        - 股价 > 100元: 搜索范围 ±8.0元
        
        Returns:
            (density, vacuum_risk): 筹码占比和真空悬崖风险
        """
        if chips_df is None or chips_df.empty:
            return 0, False
        
        # 使用技能文档标准搜索范围
        step, search_range = self._get_bin_params(close)
        
        # 计算当前价附近区间筹码占比
        low, high = close - search_range, close + search_range
        mask = (chips_df["price"] >= low) & (chips_df["price"] <= high)
        density = chips_df.loc[mask, "percent"].sum() if mask.any() else 0
        
        # 真空悬崖判断：当前价下方1个分箱粒度内筹码 < 5%
        below_1bin_mask = (chips_df["price"] >= close - step) & (chips_df["price"] < close)
        below_1bin = chips_df.loc[below_1bin_mask, "percent"].sum() if below_1bin_mask.any() else 0
        vacuum_risk = below_1bin < 5
        
        return density, vacuum_risk

    def _get_price_bins(self, chips_df: pd.DataFrame, close: float) -> np.ndarray:
        """生成价格分箱边界（np.arange 标准）
        
        分箱边界规则：
        - 从最低价向下取整到分箱粒度边界
        - 到最高价向上取整到分箱粒度边界
        - 左闭右开 [bin_start, bin_end)
        
        示例：股价 43.19, step=2.0
        - price_min 向下取整 → 40, price_max 向上取整 → 50
        - bins = [40, 42, 44, 46, 48, 50]
        - 43.19 ∈ [42, 44)
        
        Returns:
            bins: 分箱边界数组
        """
        step, _ = self._get_bin_params(close)
        
        price_min = chips_df["price"].min()
        price_max = chips_df["price"].max()
        
        # 从最低价向下取整到分箱边界
        bin_start = np.floor(price_min / step) * step
        # 到最高价向上取整到分箱边界
        bin_end = np.ceil(price_max / step) * step + step  # +step 确保包含最大值
        
        bins = np.arange(bin_start, bin_end, step)
        return bins
    
    def _get_current_bin(self, chips_df: pd.DataFrame, close: float) -> tuple[float, float]:
        """获取当前价所在分箱的区间范围（np.digitize 标准）
        
        归属规则：np.digitize(p, bins, right=False) - 1
        - 左闭右开 [bin_start, bin_end)
        - 42.00 ∈ [42, 44) ✅
        - 43.19 ∈ [42, 44) ✅
        - 44.00 ∈ [44, 46) ❌（右边界不包含，归入下一个箱）
        
        Returns:
            (bin_low, bin_high): 当前价所在分箱的上下界
        """
        bins = self._get_price_bins(chips_df, close)
        
        # np.digitize 返回的是 bins 中的索引（1-based）
        idx = np.digitize(close, bins, right=False) - 1
        
        # 确保索引在有效范围内
        idx = max(0, min(idx, len(bins) - 2))
        
        bin_low = float(bins[idx])
        bin_high = float(bins[idx + 1])
        
        return bin_low, bin_high

    def _calc_margin_change_v2(self, chips_df: pd.DataFrame, prev_chips_df: Optional[pd.DataFrame], close: float) -> tuple[float, str]:
        """计算当前价所在分箱的边际变化（技能文档推荐方法①）
        
        核心逻辑：回答"资金在当前交易价位附近是流入还是流出？"
        
        方法：取当前价所在的分箱区间（如 [42, 44)），对比2周前后该区间的筹码占比变化。
        
        示例：当前价 43.19 → 分箱 [42, 44) → 计算该区间的筹码变化
        
        Returns:
            (margin_change, direction): 变化百分点和方向描述
        """
        if prev_chips_df is None or prev_chips_df.empty or chips_df is None or chips_df.empty:
            return 0, ""
        
        # 获取当前价所在分箱
        bin_low, bin_high = self._get_current_bin(chips_df, close)
        
        # 计算当前价所在分箱的筹码占比
        curr_mask = (chips_df["price"] >= bin_low) & (chips_df["price"] < bin_high)
        prev_mask = (prev_chips_df["price"] >= bin_low) & (prev_chips_df["price"] < bin_high)
        
        curr_pct = chips_df.loc[curr_mask, "percent"].sum() if curr_mask.any() else 0
        prev_pct = prev_chips_df.loc[prev_mask, "percent"].sum() if prev_mask.any() else 0
        
        margin_change = curr_pct - prev_pct
        
        # 方向判断：筹码增加的位置含义
        # - 在当前价或更高区间增加 → 多方主动买入 ✅
        # - 在低于当前价区间增加 → 被动承接 ⚠️
        # - 在高于当前价区间大减 → 恐慌出逃 ❌
        if margin_change > 0:
            # 计算分箱中点
            bin_center = (bin_low + bin_high) / 2
            if bin_center >= close * 0.99:  # 分箱中心在当前价附近或上方
                direction = "向上集中"
            else:
                direction = "向下承接"
        else:
            direction = "减少"
        
        return margin_change, direction

    def _is_quality_low_winner(self, perf_df: pd.DataFrame, close: float) -> bool:
        """判断是否为优质低胜率（基于量化规则）
        
        技能文档标准（紫金矿业型）：
        - 年度涨幅 > 25%
        - 筹码换手完成度 > 90%
        - 成本抬升幅度 > 15%
        """
        if len(perf_df) < 2:
            return False
        
        # 年度涨幅 > 25%（技能文档标准）
        start_price = float(perf_df.iloc[0].get("weight_avg", 0))
        if start_price > 0:
            annual_return = (close - start_price) / start_price * 100
        else:
            annual_return = 0
        
        # 成本抬升幅度
        cost_rise = self._calc_cost_rise_v2(perf_df, close)[0]
        
        # 筹码换手完成度（用 winner_rate 变化幅度近似）
        start_winner = float(perf_df.iloc[0].get("winner_rate", 0))
        end_winner = float(perf_df.iloc[-1].get("winner_rate", 0))
        chip_turnover = abs(end_winner - start_winner)
        
        return annual_return > 25 and chip_turnover > 90 and cost_rise > 15

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

    def _calc_base_rating(self, total: float) -> int:
        """计算基础评级（加权评分与评级映射）— 技能文档 v2 标准
        
        加权总分范围：0 ~ 5.5
        
        映射关系：
        - ≥ 5.0 → ⭐⭐⭐⭐⭐ (5星) 最强底部信号
        - ≥ 4.0 → ⭐⭐⭐⭐ (4星) 高度指向底部
        - ≥ 2.5 → ⭐⭐⭐ (3星) 中性
        - ≥ 1.0 → ⭐⭐ (2星) 偏空
        - < 1.0 → ⭐ (1星) 回避
        """
        if total >= 5.0:
            return 5
        elif total >= 4.0:
            return 4
        elif total >= 2.5:
            return 3
        elif total >= 1.0:
            return 2
        else:
            return 1

    def _check_panic_exit(self, chips_df: pd.DataFrame, prev_chips_df: Optional[pd.DataFrame], close: float) -> bool:
        """检查是否恐慌出逃（技能文档否决项）
        
        触发条件：上方减>15% + 下方增>10%
        """
        if prev_chips_df is None or prev_chips_df.empty or chips_df is None or chips_df.empty:
            return False
        
        # 上方：当前价以上区间
        above_curr = chips_df[chips_df["price"] > close]["percent"].sum()
        above_prev = prev_chips_df[prev_chips_df["price"] > close]["percent"].sum()
        above_change = above_curr - above_prev
        
        # 下方：当前价以下区间
        below_curr = chips_df[chips_df["price"] <= close]["percent"].sum()
        below_prev = prev_chips_df[prev_chips_df["price"] <= close]["percent"].sum()
        below_change = below_curr - below_prev
        
        # 恐慌出逃：上方大幅减少(>15%) + 下方增加(>10%)
        return above_change < -15 and below_change > 10

    def _apply_veto_rules(self, dim6: dict, chips_df: Optional[pd.DataFrame] = None, prev_chips_df: Optional[pd.DataFrame] = None, close: float = 0) -> int:
        """应用否决项规则（基于量化规则）— 技能文档 v2 标准
        
        以下情况无论其他维度如何，评级不得超过 ⭐⭐：
        - 维度二（筹码密度）判定为 ❌（集中度 < 20%）
        - 维度一（边际变化）判定为 ❌ 且恐慌出逃（上方减>15% + 下方增>10%）
        - 维度四（成本抬升）判定为 ❌（< 5%）
        - 维度三（获利盘）判定为 ❌ 劣质低胜率
        """
        # 检查否决项
        if dim6["chip_density"]["label"] == "❌":
            return 2  # 最高2星
        
        # 恐慌出逃检查（使用实际计算而非文字匹配）
        if chips_df is not None and prev_chips_df is not None and close > 0:
            if self._check_panic_exit(chips_df, prev_chips_df, close):
                return 2
        elif dim6["margin_change"]["label"] == "❌" and "恐慌出逃" in dim6["margin_change"]["detail"]:
            return 2
        
        if dim6["cost_rise"]["label"] == "❌":
            return 2
        
        if dim6["winner_position"]["label"] == "❌" and "劣质低胜率" in dim6["winner_position"]["detail"]:
            return 2
        
        return 5  # 无否决项，正常评级

    def _build_result(self, perf_df: pd.DataFrame, chips_df: pd.DataFrame, prev_chips_df: Optional[pd.DataFrame], dim6: dict, close_price: float = 0, stock_name: str = "", latest_date: str = "") -> ChipDeepResult:
        """构建完整分析结果"""
        latest = perf_df.iloc[-1]
        # 使用筹码峰位成本作为平均成本，而不是 weight_avg（历史加权平均会被低价筹码拉低）
        peak_cost = self._calc_peak_cost(chips_df) if chips_df is not None and not chips_df.empty else float(latest.get("weight_avg", 0))
        weight_avg = peak_cost
        close = close_price if close_price > 0 else weight_avg
        winner_rate = float(latest.get("winner_rate", 0))
        # 使用传入的最新日期（优先使用 daily 接口的日期）
        data_date = latest_date if latest_date else str(latest.get("trade_date", ""))

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
        max_rating = self._apply_veto_rules(dim6, chips_df, prev_chips_df, close)
        base_rating = self._calc_base_rating(dim6["total"])
        rating = min(base_rating, max_rating)

        # 总结文字
        summary = self._generate_summary(close, weight_avg, winner_rate, dim6, chips_df, prev_chips_df)
        
        # 详细总结（参考范例格式）
        detailed_summary = self._generate_detailed_summary(close, weight_avg, winner_rate, dim6, perf_df, chips_df, margin_change, prev_chips_df)
        
        # 核心洞察
        core_insights = self._generate_core_insights(close, weight_avg, winner_rate, dim6, perf_df, chips_df, margin_change)
        
        # 价格阶段
        price_stages = self._calc_price_stages(perf_df)

        return ChipDeepResult(
            meta={
                "symbol": self.symbol,
                "name": stock_name,
                "analysis_date": datetime.now().strftime("%Y-%m-%d"),
                "data_date": data_date,
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

    def _generate_summary(self, close: float, weight_avg: float, winner_rate: float, dim6: dict, chips_df: Optional[pd.DataFrame] = None, prev_chips_df: Optional[pd.DataFrame] = None) -> str:
        """生成分析总结（参考范例格式）"""
        total = dim6["total"]
        # 应用与 _build_result 相同的计算逻辑，确保一致性
        max_rating = self._apply_veto_rules(dim6, chips_df, prev_chips_df, close)
        base_rating = self._calc_base_rating(total)
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

        # 底部特征（基于实际指标值判断，而非 score 的 truthy 值）
        bottom_signals = []
        # 获利盘偏冷：winner_rate < 40%（低于均衡区间）
        if winner_rate < 40:
            bottom_signals.append("获利盘偏冷")
        # 当前价低于平均成本：price_diff < 0
        if price_diff < 0:
            bottom_signals.append("当前价低于平均成本")
        # 下方有筹码支撑：支撑层级得分 > 0
        if dim6["support_level"]["score"] > 0:
            bottom_signals.append("下方有筹码支撑")

        bottom_text = "，".join(bottom_signals) if bottom_signals else "底部特征不明显"

        summary = f"""当前价 {close:.2f} {price_status}平均成本 {weight_avg:.2f}（{price_diff:+.1f}%），获利盘 {winner_rate:.1f}% {winner_desc}。{margin_desc}。六维评分 {total:.1f}/5.5，{bottom_text}。评级 {stars}。"""

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
        
        # 主力吸筹核心条件：筹码集中 + 获利盘低/合理 + 成本抬升 + 当前价低于成本
        # 边际变化是辅助确认信号，非必要条件
        if density_score and winner_score and cost_rise_score and price_diff < 0:
            if margin_score >= 2.0:
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
                content=f"六维评分 {total:.1f}/5.5，多项指标共振向好。建议在回调时分批建仓，止损位设在主要成本区下方 5-7%。",
                level="success"
            ))
        elif total >= 3:
            insights.append(CoreInsight(
                title="六维评分中等，谨慎参与",
                content=f"六维评分 {total:.1f}/5.5，部分指标向好但存在分歧。建议小仓位试探，等待信号进一步明确后再加仓。",
                level="info"
            ))
        else:
            insights.append(CoreInsight(
                title="六维评分偏弱，建议观望",
                content=f"六维评分 {total:.1f}/5.5，多数指标未达标。当前不是最佳介入时机，建议耐心等待筹码结构改善。",
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

    def _generate_detailed_summary(self, close: float, weight_avg: float, winner_rate: float, dim6: dict, perf_df: pd.DataFrame, chips_df: pd.DataFrame, margin_change: List[MarginChangeItem], prev_chips_df: Optional[pd.DataFrame] = None) -> str:
        """生成详细分析总结（参考范例格式）"""
        total = dim6["total"]
        # 应用与 _build_result 相同的计算逻辑，确保一致性
        max_rating = self._apply_veto_rules(dim6, chips_df, prev_chips_df, close)
        base_rating = self._calc_base_rating(total)
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
【综合评级】{stars}（{total:.1f}/5.5 分）
═══════════════════════════════════════

【一句话总结】
当前价 {close:.2f} {price_status}平均成本 {weight_avg:.2f}（{price_diff:+.1f}%），获利盘 {winner_rate:.1f}%。六维评分 {total:.1f}/5.5，评级 {stars}。
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
