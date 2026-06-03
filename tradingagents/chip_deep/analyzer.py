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

    def __init__(self, symbol: str, lookback_days: int = 30):
        self.symbol = symbol
        self.lookback_days = lookback_days
        self.end_date = datetime.now().strftime("%Y-%m-%d")
        # 缓冲期：30日数据需要约80天日历日以确保有足够交易日
        buffer_days = max(80, int(lookback_days * 2.5))
        self.start_date = (datetime.now() - timedelta(days=buffer_days)).strftime("%Y-%m-%d")

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
        
        # 切片：只保留最近 lookback_days 个交易日的数据用于分析
        if len(perf_df) > self.lookback_days:
            perf_df = perf_df.tail(self.lookback_days).reset_index(drop=True)
            print(f"[chip-deep] perf_df 切片: 保留最近 {self.lookback_days} 个交易日")

        # 2. 获取最新交易日（优先使用 daily 接口的最新日期，确保数据一致性）
        latest_date_raw = perf_df["trade_date"].max()
        latest_date = str(latest_date_raw).replace("-", "")
        print(f"[chip-deep] perf_df 最新日期: {latest_date} (原始: {latest_date_raw}, 类型: {type(latest_date_raw)})")
        
        # 尝试获取 daily 数据的最新日期（可能比 cyq_perf 更新）
        daily_latest_date = await self._get_daily_latest_date()
        if daily_latest_date and daily_latest_date > latest_date:
            latest_date = daily_latest_date
            print(f"[chip-deep] 使用 daily 接口的最新日期: {latest_date}")

        # 3. 获取最新日期的筹码分布 (cyq_chips)
        # 向前查找直到找到有数据的日期（处理Tushare数据延迟/周末节假日无数据）
        print(f"[chip-deep] 请求筹码分布: symbol={self.symbol}, date={latest_date}")
        chips_df = None
        valid_date = None
        # 从最新日期开始向前查找，最多尝试10个交易日
        for idx in range(len(perf_df) - 1, -1, -1):
            candidate_date = str(perf_df.iloc[idx]["trade_date"]).replace("-", "")
            if valid_date is None and candidate_date <= latest_date:
                # 第一次尝试：用原始日期
                if idx == len(perf_df) - 1:
                    print(f"[chip-deep] 尝试获取筹码分布: date={candidate_date}")
                    chips_df = await self._get_cyq_chips(candidate_date)
                    if chips_df is not None and not chips_df.empty:
                        valid_date = candidate_date
                        print(f"[chip-deep] 筹码分布获取成功: date={valid_date}")
                        break
                else:
                    # 后续尝试：用perf_df中的日期
                    print(f"[chip-deep] 向前查找筹码分布: date={candidate_date}")
                    chips_df = await self._get_cyq_chips(candidate_date)
                    if chips_df is not None and not chips_df.empty:
                        valid_date = candidate_date
                        print(f"[chip-deep] 筹码分布获取成功: date={valid_date}")
                        break
        
        if chips_df is None or chips_df.empty:
            return self._build_error_result("筹码分布数据获取失败", stock_name)
        
        # 更新latest_date为实际有数据的日期
        if valid_date and valid_date != latest_date:
            print(f"[chip-deep] 使用有效日期替代: {valid_date} (原: {latest_date})")
            latest_date = valid_date

        # 4. 获取周期起点筹码分布（边际变化）
        # 30日周期：期初约5个交易日前；60日周期：期初约10个交易日前
        prev_days = max(5, self.lookback_days // 6)
        prev_date = self._get_prev_trade_date(perf_df, latest_date, days=prev_days)
        prev_chips_df = await self._get_cyq_chips(prev_date) if prev_date else None

        # 5. 获取最新收盘价 (daily 接口)
        close_price = await self._get_close_price(latest_date)

        # 6. 计算六维评分（传入周期内成本抬升数据）
        period_cost_rise = self._calc_period_cost_rise(perf_df, latest_date)
        dim6 = self._calc_dim6(perf_df, chips_df, prev_chips_df, close_price, period_cost_rise)

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

    async def _get_daily_data(self) -> Optional[pd.DataFrame]:
        """获取30日逐日行情数据（用于趋势洞察）"""
        # 尝试缓存
        cache_key = f"{self.start_date}_{self.end_date}_daily"
        cached = get_cached(self.symbol, cache_key, "daily_data")
        if cached is not None:
            return cached

        try:
            from tradingagents.dataflows.providers.cn_tushare_provider import CnTushareProvider
            provider = CnTushareProvider()
            result = provider.get_stock_data(self.symbol, self.start_date, self.end_date)
            if result is None:
                return None
            
            import json
            if isinstance(result, str):
                try:
                    data = json.loads(result)
                    if isinstance(data, list) and len(data) > 0:
                        df = pd.DataFrame(data)
                        # 确保日期格式统一
                        if "date" in df.columns:
                            df["trade_date"] = df["date"].str.replace("-", "")
                        elif "trade_date" in df.columns:
                            df["trade_date"] = df["trade_date"].astype(str).str.replace("-", "")
                        set_cached(self.symbol, cache_key, "daily_data", df)
                        return df
                    elif isinstance(data, dict) and "error" in data:
                        return None
                except json.JSONDecodeError:
                    return None
            elif isinstance(result, pd.DataFrame) and not result.empty:
                set_cached(self.symbol, cache_key, "daily_data", result)
                return result
        except Exception as e:
            print(f"[chip-deep] _get_daily_data error: {e}")
        return None

    async def _get_cyq_chips(self, trade_date: str) -> Optional[pd.DataFrame]:
        """获取指定日期的筹码分布（带缓存）"""
        # 尝试缓存
        cached = get_cached(self.symbol, trade_date, "cyq_chips")
        if cached is not None:
            return cached

        try:
            print(f"[chip-deep] 请求 cyq_chips: symbol={self.symbol}, trade_date={trade_date}")
            result = route_to_vendor(
                "get_cyq_chips",
                symbol=self.symbol,
                trade_date=trade_date,
            )
            print(f"[chip-deep] cyq_chips 结果: {type(result)}, {result}")
            if result is None:
                print(f"[chip-deep] cyq_chips 返回 None")
                return None
            df = result if isinstance(result, pd.DataFrame) else None
            if df is not None:
                set_cached(self.symbol, trade_date, "cyq_chips", df)
                print(f"[chip-deep] cyq_chips 数据: {len(df)} 行, 列: {df.columns.tolist()}")
            else:
                print(f"[chip-deep] cyq_chips 结果不是 DataFrame")
            return df
        except Exception as e:
            print(f"[chip-deep] cyq_chips 异常: {e}")
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

    def _calc_dim6(self, perf_df: pd.DataFrame, chips_df: pd.DataFrame, prev_chips_df: Optional[pd.DataFrame], close_price: float = 0, period_cost_rise: dict = None) -> dict:
        """六维评分计算（基于量化规则）— 技能文档 v2 加权版（适配多周期）

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
        
        # 周期内成本抬升数据（适配30/60/250日）
        if period_cost_rise is None:
            period_cost_rise = self._calc_period_cost_rise(perf_df, str(latest.get("trade_date", "")))

        # 获取分箱参数
        step, search_range = self._get_bin_params(close)

        # ① 边际变化（★ 高权重维度，基础分 2.0）
        # 使用技能文档 v2 完整判定树
        margin_score, margin_label, margin_desc, panic_exit = self._calc_margin_change_v2(
            chips_df, prev_chips_df, close
        )
        margin_detail = margin_desc

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
            # 区分优质/劣质低胜率（传入周期成本抬升数据）
            is_quality = self._is_quality_low_winner(perf_df, close, period_cost_rise)
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
        # 使用周期内成本抬升数据（适配30/60/250日）
        cost_rise = period_cost_rise.get("cost_rise", 0)
        price_rise = period_cost_rise.get("price_rise", 0)
        cost_rise_type = period_cost_rise.get("type_desc", "未知")
        
        # 规则 4a：成本抬升幅度（按周期动态调整阈值）
        if self.lookback_days <= 30:
            # 30日周期：月度涨幅标准
            if cost_rise > 8:
                cost_rise_score = 0.5
                cost_rise_label = "✅✅"
                cost_rise_desc = "月度成本大幅抬高"
            elif cost_rise > 4:
                cost_rise_score = 0.5
                cost_rise_label = "✅"
                cost_rise_desc = "月度成本明显上移"
            elif cost_rise > 1:
                cost_rise_score = 0.25
                cost_rise_label = "⚠️"
                cost_rise_desc = "月度成本部分抬高"
            else:
                cost_rise_score = 0.0
                cost_rise_label = "❌"
                cost_rise_desc = "月度成本基本没变"
        elif self.lookback_days <= 60:
            # 60日周期：季度标准
            if cost_rise > 15:
                cost_rise_score = 0.5
                cost_rise_label = "✅✅"
                cost_rise_desc = "季度成本大幅抬高"
            elif cost_rise > 8:
                cost_rise_score = 0.5
                cost_rise_label = "✅"
                cost_rise_desc = "季度成本明显上移"
            elif cost_rise > 3:
                cost_rise_score = 0.25
                cost_rise_label = "⚠️"
                cost_rise_desc = "季度成本部分抬高"
            else:
                cost_rise_score = 0.0
                cost_rise_label = "❌"
                cost_rise_desc = "季度成本基本没变"
        else:
            # 250日周期：年度标准（原有逻辑）
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
            "margin_change": {
                "score": margin_score, 
                "label": margin_label, 
                "detail": margin_detail,
                "panic_exit": panic_exit,  # 技能文档 v2：恐慌出逃标志
            },
            "winner_position": {"score": winner_score, "label": winner_label, "detail": winner_detail},
            "cost_rise": {"score": cost_rise_score, "label": cost_rise_label, "detail": cost_rise_detail},
            "overshoot": {"score": overshoot_score, "label": overshoot_label, "detail": overshoot_detail},
            "support_level": {"score": support_score, "label": support_label, "detail": support_detail},
            "total": total,
            "margin_direction_type": "active_buy" if margin_score >= 2.0 else "mild_up" if margin_score >= 0.5 else "no_support",  # 用于核心洞察
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
    
    def _aggregate_to_bins(self, chips_df: pd.DataFrame, bins: np.ndarray) -> np.ndarray:
        """将筹码分布聚合到分箱
        
        Args:
            chips_df: 筹码分布数据
            bins: 分箱边界
            
        Returns:
            每个分箱的筹码占比数组
        """
        binned = np.zeros(len(bins) - 1)
        for _, row in chips_df.iterrows():
            p = row["price"]
            pc = row["percent"]
            idx = np.digitize(p, bins, right=False) - 1
            if 0 <= idx < len(binned):
                binned[idx] += pc
        return binned
    
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

    def _calc_margin_change_v2(self, chips_df: pd.DataFrame, prev_chips_df: Optional[pd.DataFrame], close: float) -> tuple[float, str, str, bool]:
        """计算边际变化（技能文档 v2 双轨制）
        
        双轨制：取当前分箱与筹码峰分箱中绝对值更大的变化
        
        核心逻辑：回答"资金在往哪个方向流动？"
        
        判定树（按优先级）：
        1. chg > 10%     → 2.0分 ✅ 猛烈向上
        2. chg >= 3%     → 0.5分 ⚠️ 温和
        3. chg < -15%    → 检查下方分箱变化
           - 下方大增 >10% → 0分 ❌ 恐慌出逃 + 否决项
           - 下方无大增   → 0分 ❌ 减少
        4. chg < 0       → 0分 ❌ 减少
        5. |chg| < 3%    → 0分 ❌ 无人
        
        Returns:
            (score, label, detail, panic_exit): 得分、标签、描述、是否恐慌出逃
        """
        if prev_chips_df is None or prev_chips_df.empty or chips_df is None or chips_df.empty:
            return 0, "❌", "数据不足", False
        
        # 获取分箱参数
        step = self._get_bin_params(close)[0]
        bins = self._get_price_bins(chips_df, close)
        
        # 聚合当前筹码分布到分箱
        bp_current = self._aggregate_to_bins(chips_df, bins)
        bp_prev = self._aggregate_to_bins(prev_chips_df, bins)
        
        # 双轨制：当前分箱 vs 筹码峰分箱
        close_bin = np.digitize(close, bins, right=False) - 1
        close_bin = max(0, min(close_bin, len(bp_current) - 1))
        
        peak_bin = int(np.argmax(bp_current))
        
        chg_close = bp_current[close_bin] - bp_prev[close_bin]
        chg_peak = bp_current[peak_bin] - bp_prev[peak_bin]
        
        # 取绝对值更大的那个
        if abs(chg_close) >= abs(chg_peak):
            chg = chg_close
            source = "当前分箱"
            source_bin = close_bin
        else:
            chg = chg_peak
            source = "筹码峰分箱"
            source_bin = peak_bin
        
        # 分箱区间描述
        bin_start = bins[source_bin]
        bin_end = bins[source_bin + 1] if source_bin + 1 < len(bins) else bins[source_bin] + step
        
        # 方向判断：chg 符号天然决定方向
        direction = "向上" if chg > 0 else "向下" if chg < 0 else "持平"
        
        # ┌─────────────────────────────────────────────────────────┐
        # │           边际变化完整判定树（技能文档 v2）              │
        # └─────────────────────────────────────────────────────────┘
        
        # 1. 猛烈向上
        if chg > 10:
            return 2.0, "✅", f"[{bin_start:.0f},{bin_end:.0f})+{chg:.1f}% {direction}（{source}）", False
        
        # 2. 温和
        elif chg >= 3:
            return 0.5, "⚠️", f"[{bin_start:.0f},{bin_end:.0f})+{chg:.1f}% {direction}（{source}）", False
        
        # 3. 恐慌出逃检测
        elif chg < -15:
            # 计算下方分箱（低于当前价的所有分箱）的总变化
            below_curr_mask = chips_df["price"] < close
            below_prev_mask = prev_chips_df["price"] < close
            below_curr_pct = chips_df.loc[below_curr_mask, "percent"].sum() if below_curr_mask.any() else 0
            below_prev_pct = prev_chips_df.loc[below_prev_mask, "percent"].sum() if below_prev_mask.any() else 0
            below_chg = below_curr_pct - below_prev_pct
            
            if below_chg > 10:
                return 0, "❌", f"[{bin_start:.0f},{bin_end:.0f}){chg:.1f}% {direction}（{source}）恐慌出逃", True
            else:
                return 0, "❌", f"[{bin_start:.0f},{bin_end:.0f}){chg:.1f}% {direction}（{source}）", False
        
        # 4. 减少
        elif chg < 0:
            return 0, "❌", f"[{bin_start:.0f},{bin_end:.0f}){chg:.1f}% {direction}（{source}）", False
        
        # 5. 无人
        else:
            return 0, "❌", f"[{bin_start:.0f},{bin_end:.0f}){chg:+.1f}% {direction}（{source}）", False

    def _is_quality_low_winner(self, perf_df: pd.DataFrame, close: float, period_cost_rise: dict = None) -> bool:
        """判断是否为优质低胜率（基于量化规则，适配多周期）
        
        技能文档标准（紫金矿业型）：
        - 年度涨幅 > 25%
        - 筹码换手完成度 > 90%
        - 成本抬升幅度 > 15%
        
        30日周期适配：使用年化收益率替代年度涨幅，月度换手替代年度换手
        """
        if len(perf_df) < 2:
            return False
        
        # 获取周期内成本抬升数据
        if period_cost_rise is None:
            latest_date = str(perf_df.iloc[-1].get("trade_date", ""))
            period_cost_rise = self._calc_period_cost_rise(perf_df, latest_date)
        
        annual_return = period_cost_rise.get("annual_return", 0)
        cost_rise = period_cost_rise.get("cost_rise", 0)
        period_days = period_cost_rise.get("period_days", 30)
        
        # 筹码换手完成度（用 winner_rate 变化幅度近似）
        start_winner = float(perf_df.iloc[0].get("winner_rate", 0))
        end_winner = float(perf_df.iloc[-1].get("winner_rate", 0))
        chip_turnover = abs(end_winner - start_winner)
        
        # 按周期动态调整阈值
        if self.lookback_days <= 30:
            # 30日周期：月度标准（年化25% ≈ 月度2%，放宽到5%）
            # 月度换手 > 15%（年化约90%的等比例）
            # 月度成本抬升 > 2%（年化约15%的等比例）
            return annual_return > 5 and chip_turnover > 15 and cost_rise > 2
        elif self.lookback_days <= 60:
            # 60日周期：季度标准
            return annual_return > 15 and chip_turnover > 45 and cost_rise > 5
        else:
            # 250日周期：年度标准（原有逻辑）
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

    def _calc_period_cost_rise(self, perf_df: pd.DataFrame, latest_date: str) -> dict:
        """计算周期内成本抬升（适配30/60/250日不同周期）
        
        根据 lookback_days 动态调整判断标准：
        - 30日周期：周期涨幅作为"月度涨幅"，判断优质低胜率
        - 60日/250日：维持原有年度涨幅标准
        
        Returns:
            dict: {cost_rise, price_rise, type_desc, period_return, period_days}
        """
        if len(perf_df) < 2:
            return {"cost_rise": 0, "price_rise": 0, "type_desc": "数据不足", "period_return": 0, "period_days": 0}
        
        # 按日期排序
        df_sorted = perf_df.sort_values("trade_date")
        
        # 取周期起止数据
        start_avg = float(df_sorted.iloc[0].get("weight_avg", 0))
        end_avg = float(df_sorted.iloc[-1].get("weight_avg", 0))
        start_date = df_sorted.iloc[0]["trade_date"]
        end_date = df_sorted.iloc[-1]["trade_date"]
        
        # 计算日历天数
        from datetime import datetime as dt
        try:
            start_dt = dt.strptime(str(start_date), "%Y%m%d")
            end_dt = dt.strptime(str(end_date), "%Y%m%d")
            period_days = (end_dt - start_dt).days
        except:
            period_days = len(df_sorted)
        
        # 成本抬升幅度
        cost_rise = ((end_avg - start_avg) / start_avg * 100) if start_avg > 0 else 0
        
        # 周期收益率（年化）
        start_close = 0.0
        end_close = 0.0
        if period_days > 0 and start_avg > 0:
            # 使用期初收盘价计算更准确
            start_close = float(df_sorted.iloc[0].get("close", start_avg))
            end_close = float(df_sorted.iloc[-1].get("close", end_avg))
            if start_close > 0:
                total_return = (end_close - start_close) / start_close * 100
                # 年化收益率
                annual_return = total_return * 365 / period_days
            else:
                total_return = cost_rise
                annual_return = total_return * 365 / period_days
        else:
            total_return = 0
            annual_return = 0
        
        # 股价涨幅（使用期初/期末收盘价计算，与成本抬升对比）
        if start_close > 0:
            price_rise = total_return
        else:
            price_rise = cost_rise
        
        # 类型判断：成本涨幅 vs 股价涨幅
        diff = cost_rise - price_rise
        if abs(diff) <= 10:
            type_desc = "健康换手型"
        elif cost_rise > price_rise:
            type_desc = "底部抬升型"
        else:
            type_desc = "追高套牢型"
        
        return {
            "cost_rise": cost_rise,
            "price_rise": price_rise,
            "type_desc": type_desc,
            "period_return": total_return,
            "annual_return": annual_return,
            "period_days": period_days,
        }

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

    def _apply_veto_rules(self, dim6: dict, chips_df: Optional[pd.DataFrame] = None, prev_chips_df: Optional[pd.DataFrame] = None, close: float = 0) -> tuple[int, str]:
        """应用否决项规则（基于量化规则）— 技能文档 v2 修正版

        否决项设计原则：
        - 仅对"极端风险信号"一票否决（恐慌出逃、筹码真空）
        - 辅助维度（成本抬升、劣质低胜率）降级为"扣分项"，不封顶评级

        一票否决（评级不得超过 ⭐⭐）：
        - 维度二（筹码密度）判定为 ❌（集中度 < 20%）→ 无支撑，破位加速跌
        - 维度一（边际变化）恐慌出逃（上方减>15% + 下方增>10%）→ 资金踩踏

        扣分项（仅影响该维度得分，已体现在评分中，不额外封顶）：
        - 维度四（成本抬升）❌ → 底部未抬高，但该维度权重仅0.5，不一票否决
        - 维度三（获利盘）劣质低胜率 ❌ → 已得0分，不再额外封顶

        Returns:
            (max_rating, veto_reason): 最高允许评级、否决原因描述
        """
        # 一票否决：极端风险信号
        if dim6["chip_density"]["label"] == "❌":
            return 2, "筹码密度薄弱（集中度<20%，无支撑）"

        if dim6["margin_change"].get("panic_exit", False):
            return 2, "恐慌出逃（上方减>15%且下方增>10%，资金踩踏）"

        # 扣分项（不再一票否决，仅记录提示）
        if dim6["cost_rise"]["label"] == "❌":
            # 成本抬升权重仅0.5，辅助维度不应一票否决
            return 5, "成本未抬升（底部未抬高，该维度得0分）"

        if dim6["winner_position"]["label"] == "❌" and "劣质低胜率" in dim6["winner_position"]["detail"]:
            # 劣质低胜率已在获利盘维度得0分，不再额外封顶
            return 5, "劣质低胜率（弱势股，该维度得0分）"

        return 5, ""  # 无否决项

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

        # 边际变化数据（使用统一分箱标准）
        margin_change = []
        if prev_chips_df is not None and not prev_chips_df.empty and chips_df is not None and not chips_df.empty:
            # 使用与 _calc_margin_change_v2 相同的分箱标准
            bins = self._get_price_bins(chips_df, close)
            
            # 按分箱聚合筹码数据
            def _aggregate_by_bins(df: pd.DataFrame, bins: np.ndarray) -> dict:
                """将筹码数据按分箱聚合"""
                result = {}
                for i in range(len(bins) - 1):
                    bin_low, bin_high = bins[i], bins[i + 1]
                    mask = (df["price"] >= bin_low) & (df["price"] < bin_high)
                    pct = df.loc[mask, "percent"].sum() if mask.any() else 0
                    result[(float(bin_low), float(bin_high))] = pct
                return result
            
            curr_by_bin = _aggregate_by_bins(chips_df, bins)
            prev_by_bin = _aggregate_by_bins(prev_chips_df, bins)
            
            # 计算每个分箱的变化
            for (bin_low, bin_high), curr_pct in curr_by_bin.items():
                prev_pct = prev_by_bin.get((bin_low, bin_high), 0)
                change = curr_pct - prev_pct
                if abs(change) > 0.5:  # 只记录显著变化（阈值放宽到0.5）
                    margin_change.append(MarginChangeItem(
                        price_low=round(bin_low, 2),
                        price_high=round(bin_high, 2),
                        prev_pct=round(prev_pct, 2),
                        curr_pct=round(curr_pct, 2),
                        change=round(change, 2),
                    ))
            
            # 按变化幅度排序，取前5
            margin_change = sorted(margin_change, key=lambda x: abs(x.change), reverse=True)[:5]

        # 评级计算（应用否决项规则）
        max_rating, veto_reason = self._apply_veto_rules(dim6, chips_df, prev_chips_df, close)
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
            veto_reason=veto_reason,
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

    def _generate_one_liner(self, close: float, weight_avg: float, winner_rate: float, dim6: dict, total: float, price_diff: float) -> str:
        """生成一句话总结（参考范例格式）

        范例：
        ⭐⭐⭐ 中性偏持有。获利盘 42.21% 均衡（最健康）、价格 38.8 几乎精确贴合成本 38.1（+1.8%）、
        厚垫子 56%——这些是持有理由。边际 +6.4% 温和（0.5分）和成本抬升有限（+8.1%）是评分仅 3.75 的原因。
        已有仓位继续持有（股息率 4.2%）；没有仓位的不急于买入，等边际变化转强或回调至 38。
        """
        # 评级（应用否决项规则，确保与最终 rating 一致）
        max_rating, veto_reason = self._apply_veto_rules(dim6, None, None, close)
        base_rating = self._calc_base_rating(total)
        rating = min(base_rating, max_rating)
        stars = "⭐" * rating
        
        # 评级定性（使用最终 rating，而非 max_rating）
        if rating >= 4:
            stance = "积极看多"
        elif rating == 3:
            stance = "中性偏持有"
        elif rating == 2:
            stance = "中性偏观望"
        else:
            stance = "建议回避"
        
        # 获利盘定性
        if winner_rate < 20:
            winner_qual = "偏冷（恐慌）"
        elif winner_rate < 40:
            winner_qual = "偏低"
        elif winner_rate < 60:
            winner_qual = "均衡（最健康）"
        elif winner_rate < 80:
            winner_qual = "偏高"
        else:
            winner_qual = "过热"
        
        # 价格位置定性
        if abs(price_diff) < 3:
            price_qual = f"几乎精确贴合成本 {weight_avg:.2f}（{price_diff:+.1f}%）"
        elif price_diff < 0:
            price_qual = f"低于成本 {weight_avg:.2f}（{price_diff:+.1f}%）"
        else:
            price_qual = f"高于成本 {weight_avg:.2f}（{price_diff:+.1f}%）"
        
        # 下方支撑（厚垫子）
        support_score = dim6["support_level"]["score"]
        if support_score >= 0.5:
            support_desc = f"下方有 {int(support_score * 8)} 层支撑"
        else:
            support_desc = "下方支撑薄弱"
        
        # 边际变化
        margin_score = dim6["margin_change"]["score"]
        margin_detail = dim6["margin_change"]["detail"]
        if margin_score >= 2.0:
            margin_desc = f"边际变化积极（{margin_detail}，{margin_score}分）"
        elif margin_score >= 0.5:
            margin_desc = f"边际变化温和（{margin_detail}，{margin_score}分）"
        else:
            margin_desc = f"边际变化不足（{margin_detail}，{margin_score}分）"
        
        # 成本抬升
        cost_rise_score = dim6["cost_rise"]["score"]
        cost_rise_detail = dim6["cost_rise"]["detail"]
        if cost_rise_score >= 0.5:
            cost_desc = f"成本抬升明显（{cost_rise_detail}）"
        else:
            cost_desc = f"成本抬升有限（{cost_rise_detail}）"
        
        # 组装一句话总结
        parts = []
        parts.append(f"{stars} {stance}。")
        
        # 正面因素（持有/买入理由）
        positives = []
        if 40 <= winner_rate < 60:
            positives.append(f"获利盘 {winner_rate:.1f}% {winner_qual}")
        if abs(price_diff) < 5:
            positives.append(price_qual)
        if support_score >= 0.5:
            positives.append(support_desc)
        
        if positives:
            parts.append(f"{'、'.join(positives)}——这些是{'持有' if rating >= 3 else '关注'}理由。")

        # 负面因素（评分受限原因）
        negatives = []
        if margin_score < 2.0:
            negatives.append(margin_desc)
        if cost_rise_score < 0.5:
            negatives.append(cost_desc)
        if winner_rate >= 80:
            negatives.append(f"获利盘 {winner_rate:.1f}% 过热")
        if veto_reason:
            negatives.append(f"否决项：{veto_reason}")

        if negatives:
            parts.append(f"{'、'.join(negatives)}是评分仅 {total:.1f} 的原因。")

        # 操作建议
        if rating >= 4:
            parts.append("建议在回调时分批建仓，止损位设在主要成本区下方 5-7%。")
        elif rating == 3:
            parts.append("已有仓位可继续持有；没有仓位的不急于买入，等待信号进一步明确。")
        elif rating == 2:
            parts.append("建议观望，等待筹码结构改善或价格回调至成本区附近再考虑。")
        else:
            parts.append("当前风险大于机会，建议回避或减仓。")
        
        return "".join(parts)

    def _generate_summary(self, close: float, weight_avg: float, winner_rate: float, dim6: dict, chips_df: Optional[pd.DataFrame] = None, prev_chips_df: Optional[pd.DataFrame] = None) -> str:
        """生成分析总结（参考范例格式）"""
        total = dim6["total"]
        # 应用与 _build_result 相同的计算逻辑，确保一致性
        max_rating, veto_reason = self._apply_veto_rules(dim6, chips_df, prev_chips_df, close)
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

        if veto_reason:
            summary += f" ⚠️ {veto_reason}"

        return summary

    def _generate_core_insights(self, close: float, weight_avg: float, winner_rate: float, dim6: dict, perf_df: pd.DataFrame, chips_df: pd.DataFrame, margin_change: List[MarginChangeItem]) -> List[CoreInsight]:
        """生成核心洞察列表 — 资深投资专家视角，提供可操作的建仓/加仓/止盈/止损建议"""
        insights = []
        price_diff = ((close - weight_avg) / weight_avg * 100) if weight_avg else 0
        total = dim6["total"]
        rating = min(self._calc_base_rating(total), self._apply_veto_rules(dim6, chips_df, None, close)[0])
        
        # 默认价位（fallback）
        stop_loss = close * 0.93
        take_profit = close * 1.15
        strongest_support = close * 0.95
        first_resistance = close * 1.10
        main_cost = weight_avg if weight_avg else close
        
        # 提取筹码分布的关键价位
        key_levels = {}
        if chips_df is not None and not chips_df.empty:
            chips_sorted = chips_df.sort_values("percent", ascending=False)
            top_zone = chips_sorted.iloc[0]
            key_levels["main_cost"] = top_zone["price"]
            key_levels["main_cost_pct"] = top_zone["percent"]
            
            # 计算前3大筹码密集区作为支撑位
            support_zones = []
            for _, row in chips_sorted.head(3).iterrows():
                if row["price"] <= close:  # 只取当前价下方的筹码峰
                    support_zones.append((row["price"], row["percent"]))
            key_levels["supports"] = support_zones
            
            # 上方压力区（当前价上方的筹码峰）
            resistance_zones = []
            for _, row in chips_sorted.head(5).iterrows():
                if row["price"] > close:
                    resistance_zones.append((row["price"], row["percent"]))
            key_levels["resistances"] = resistance_zones
        
        # ========== 模块1：一句话总览 ==========
        one_liner = self._generate_one_liner(close, weight_avg, winner_rate, dim6, total, price_diff)
        insights.append(CoreInsight(
            title="一句话总结",
            content=one_liner,
            level="info"
        ))
        
        # ========== 模块2：关键价位（支撑/压力/止损/止盈） ==========
        if key_levels.get("main_cost"):
            main_cost = key_levels["main_cost"]
            supports = key_levels.get("supports", [])
            resistances = key_levels.get("resistances", [])
            
            # 计算具体操作价位
            if supports:
                strongest_support = max(supports, key=lambda x: x[1])[0]  # 筹码最多的支撑位
                stop_loss = strongest_support * 0.93  # 支撑位下方 7%
            else:
                stop_loss = main_cost * 0.93
            
            if resistances:
                first_resistance = min(resistances, key=lambda x: x[0])[0]  # 最近的上方压力
                take_profit = first_resistance * 1.02  # 压力位上方 2%
            else:
                take_profit = close * 1.15  # 无明确压力时给 15% 目标
            
            # 构建价位信息
            level_parts = []
            level_parts.append(f"主力成本 {main_cost:.2f}")
            if supports:
                level_parts.append(f"强支撑 {strongest_support:.2f}")
            if resistances:
                level_parts.append(f"首压力 {first_resistance:.2f}")
            level_parts.append(f"建议止损 {stop_loss:.2f}")
            level_parts.append(f"建议止盈 {take_profit:.2f}")
            
            insights.append(CoreInsight(
                title="关键价位",
                content=" | ".join(level_parts),
                level="info"
            ))
        
        # ========== 模块3：主力意图研判（深度分析） ==========
        density_score = dim6["chip_density"]["score"]
        margin_score = dim6["margin_change"]["score"]
        winner_score = dim6["winner_position"]["score"]
        cost_rise_score = dim6["cost_rise"]["score"]
        
        if density_score and cost_rise_score and price_diff < 0:
            # 价低于成本 + 筹码集中 + 成本抬升 = 主力吸筹
            if margin_score >= 2.0:
                insights.append(CoreInsight(
                    title="🔴 主力积极吸筹 — 建议关注",
                    content=f"当前价 {close:.2f} 低于平均成本 {weight_avg:.2f}（折价{abs(price_diff):.1f}%），筹码集中度良好且底部持续抬高。边际变化积极确认资金主动流入。"
                            f"建议：若当前无仓位，可在 {close:.2f} 附近开始分批建仓（30%-50%目标仓位），止损设在 {stop_loss:.2f}。",
                    level="success"
                ))
            else:
                insights.append(CoreInsight(
                    title="主力可能处于吸筹初期",
                    content=f"筹码集中且成本抬升，但边际变化尚温和，可能是吸筹初期。建议：保持关注，若后续出现放量阳线确认再介入。",
                    level="info"
                ))
        elif winner_rate > 85:
            # 获利盘极高，风险信号
            insights.append(CoreInsight(
                title="⚠️ 获利盘极高 — 建议减仓或止盈",
                content=f"获利盘高达 {winner_rate:.1f}%，绝大多数持仓者处于盈利状态，抛压随时可能出现。"
                        f"建议：已有仓位者建议在 {take_profit:.2f} 附近逢高减仓（至少减仓 50%），锁定利润；无仓位者不建议追高。",
                level="danger"
            ))
        elif winner_rate > 70:
            insights.append(CoreInsight(
                title="获利盘偏高 — 注意控制仓位",
                content=f"获利盘 {winner_rate:.1f}% 处于偏高水平，短期存在获利了结压力。"
                        f"建议：控制仓位不超过 30%，若突破 {take_profit:.2f} 可加仓至 50%。",
                level="warning"
            ))
        elif not density_score:
            insights.append(CoreInsight(
                title="筹码分散 — 观望为主",
                content="筹码集中度不足，缺乏主力资金运作痕迹。建议等待筹码在某一价位区间重新集中后再考虑布局。",
                level="warning"
            ))
        
        # ========== 模块4：成本抬升深度分析 ==========
        cost_rise_label = dim6["cost_rise"]["label"]
        cost_rise_detail = dim6["cost_rise"].get("detail", "")
        
        if cost_rise_label == "✅✅":
            insights.append(CoreInsight(
                title="✅ 成本大幅抬升 — 上涨动能强劲",
                content=f"{cost_rise_detail}。成本快速抬升表明资金积极换手推高底部，这是典型的上升趋势特征。"
                        f"建议：可积极持有，止损位上移至 {stop_loss:.2f}。",
                level="success"
            ))
        elif cost_rise_label == "✅":
            insights.append(CoreInsight(
                title="✅ 成本温和抬升 — 趋势健康",
                content=f"{cost_rise_detail}。成本温和抬升表明上涨节奏稳健，不急于追高。"
                        f"建议：耐心持有，若放量突破 {take_profit:.2f} 可考虑加仓。",
                level="success"
            ))
        elif cost_rise_label == "⚠️":
            insights.append(CoreInsight(
                title="⚠️ 成本抬升放缓 — 警惕动能衰减",
                content=f"{cost_rise_detail}。成本抬升力度减弱，需观察后续是否重新加速。"
                        f"建议：持有者可适当降低仓位至 30%，等待方向明朗。",
                level="warning"
            ))
        elif cost_rise_label == "❌":
            insights.append(CoreInsight(
                title="❌ 成本未抬升 — 缺乏持续上涨动能",
                content=f"{cost_rise_detail}。成本停滞表明资金观望情绪浓厚，短期缺乏推升力量。"
                        f"建议：无仓位者暂不介入；已有仓位者关注 {stop_loss:.2f} 支撑，跌破需果断止损。",
                level="warning"
            ))
        
        # ========== 模块5：价格走势阶段 + 操作指引 ==========
        stages = self._calc_price_stages(perf_df)
        if stages:
            latest_stage = stages[-1]
            if latest_stage.name == "大涨":
                insights.append(CoreInsight(
                    title=f"📈 处于上涨阶段（+{latest_stage.change_pct:.1f}%）",
                    content=f"从 {latest_stage.start_price:.2f} 涨至 {latest_stage.end_price:.2f}，获利盘从 {latest_stage.winner_rate_start:.1f}% 升至 {latest_stage.winner_rate_end:.1f}%。"
                            f"建议：持有者可在 {take_profit:.2f} 附近分批止盈；无仓位者等待回调至 {strongest_support:.2f} 附近再考虑介入。",
                    level="info"
                ))
            elif latest_stage.name == "深度回调":
                insights.append(CoreInsight(
                    title=f"📉 处于深度回调阶段（-{abs(latest_stage.change_pct):.1f}%）",
                    content=f"从高点 {latest_stage.start_price:.2f} 回调至 {latest_stage.end_price:.2f}。"
                            f"建议：若六维评分 ≥ 3.0，此阶段可能是分批建仓良机，首笔仓位控制在 20%-30%，止损设在 {stop_loss:.2f}。",
                    level="info"
                ))
        
        # ========== 模块6：综合操作策略 ==========
        if rating >= 4:
            insights.append(CoreInsight(
                title=f"⭐⭐⭐⭐ 综合评级优秀 — 建议积极布局",
                content=f"六维评分 {total:.1f}/5.5，评级 {rating} 星。筹码结构健康，多项指标共振向好。"
                        f"操作建议：无仓位者建议分批建仓（30%→50%→70%），已有仓位者可持有或适度加仓。"
                        f"关键价位：止损 {stop_loss:.2f}，止盈 {take_profit:.2f}。",
                level="success"
            ))
        elif rating == 3:
            insights.append(CoreInsight(
                title=f"⭐⭐⭐ 综合评级中等 — 建议谨慎参与",
                content=f"六维评分 {total:.1f}/5.5，评级 {rating} 星。部分指标向好但存在分歧。"
                        f"操作建议：小仓位试探（不超过 30%），等待筹码结构改善或出现放量阳线信号后再加仓。"
                        f"关键价位：止损 {stop_loss:.2f}，止盈 {take_profit:.2f}。",
                level="info"
            ))
        else:
            insights.append(CoreInsight(
                title=f"⭐ 综合评级偏弱 — 建议观望",
                content=f"六维评分 {total:.1f}/5.5，评级 {rating} 星。多数指标未达标，筹码结构不佳。"
                        f"操作建议：不建议介入，等待筹码集中度提升或出现明确底部信号。"
                        f"关注信号：筹码在 {main_cost:.2f} 附近重新集中、放量突破。",
                level="warning"
            ))
        
        # ========== 模块7：风险提示 ==========
        if not dim6["support_level"]["score"]:
            insights.append(CoreInsight(
                title="⚠️ 下方支撑薄弱",
                content="当前价下方筹码稀疏，形成'真空悬崖'。一旦跌破关键位可能引发连锁抛售。"
                        f"建议：严格止损在 {stop_loss:.2f}，不可扛单。",
                level="danger"
            ))
        
        # 否决项提示
        veto_rating, veto_reason = self._apply_veto_rules(dim6, chips_df, None, close)
        if veto_rating < 5 and veto_reason:
            insights.append(CoreInsight(
                title=f"⛔ 评级受限：{veto_reason}",
                content=f"因{veto_reason}，综合评级被限制为 {veto_rating} 星。即使评分较高也需谨慎对待。",
                level="danger" if veto_rating <= 2 else "warning"
            ))
        
        return insights

    def _generate_detailed_summary(self, close: float, weight_avg: float, winner_rate: float, dim6: dict, perf_df: pd.DataFrame, chips_df: pd.DataFrame, margin_change: List[MarginChangeItem], prev_chips_df: Optional[pd.DataFrame] = None) -> str:
        """生成详细分析总结（参考范例格式）"""
        total = dim6["total"]
        # 应用与 _build_result 相同的计算逻辑，确保一致性
        max_rating, _ = self._apply_veto_rules(dim6, chips_df, prev_chips_df, close)
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
