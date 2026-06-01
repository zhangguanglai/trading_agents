"""chip-deep Pydantic 数据模型"""

from pydantic import BaseModel, Field
from typing import List, Optional


class ChipDistributionItem(BaseModel):
    """筹码分布单项"""
    price_low: float = Field(..., description="价格区间下限")
    price_high: float = Field(..., description="价格区间上限")
    percent: float = Field(..., description="该区间筹码占比(%)")


class MarginChangeItem(BaseModel):
    """边际变化单项"""
    price_low: float
    price_high: float
    prev_pct: float = Field(..., description="前期占比(%)")
    curr_pct: float = Field(..., description="当期占比(%)")
    change: float = Field(..., description="变化百分点")


class Dim6ScoreItem(BaseModel):
    """六维评分单项"""
    score: int = Field(..., ge=0, le=1, description="0=否, 1=是")
    label: str = Field(..., description="✅/⚠️/❌")
    detail: str = Field(..., description="详细说明")


class Dim6Score(BaseModel):
    """六维评分总览"""
    chip_density: Dim6ScoreItem = Field(..., description="① 筹码密度")
    margin_change: Dim6ScoreItem = Field(..., description="② 边际变化")
    winner_position: Dim6ScoreItem = Field(..., description="③ 获利盘")
    cost_rise: Dim6ScoreItem = Field(..., description="④ 成本抬升")
    overshoot: Dim6ScoreItem = Field(..., description="⑤ 超跌程度")
    support_level: Dim6ScoreItem = Field(..., description="⑥ 下方支撑")


class PriceStage(BaseModel):
    """价格走势阶段"""
    name: str = Field(..., description="阶段名称")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    start_price: float = Field(..., description="开始价格")
    end_price: float = Field(..., description="结束价格")
    change_pct: float = Field(..., description="涨跌幅(%)")
    winner_rate_start: float = Field(..., description="开始获利盘(%)")
    winner_rate_end: float = Field(..., description="结束获利盘(%)")


class ChipDeepResult(BaseModel):
    """筹码深度分析完整结果"""
    meta: dict = Field(..., description="元数据")
    current: dict = Field(..., description="当前价格/成本指标")
    price_stages: List[PriceStage] = Field(default=[], description="价格走势阶段")
    chip_distribution: List[ChipDistributionItem] = Field(..., description="筹码分布")
    margin_change_2w: List[MarginChangeItem] = Field(..., description="2周边际变化")
    dim6_score: Dim6Score = Field(..., description="六维评分")
    dim6_total: int = Field(..., ge=0, le=6, description="六维总分")
    rating: int = Field(..., ge=1, le=5, description="综合评级 1-5星")
    summary_text: str = Field(..., description="一句话总结")
    detailed_summary: str = Field(default="", description="详细分析总结（参考范例格式）")
