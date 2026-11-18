"""Pydantic request/response models for the TradingAgents API.

Extracted from ``api.main`` to keep the routing module focused on app
assembly. All models are re-exported from ``api.main`` so existing
``from api.main import <Model>`` imports continue to work.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_serializer

from tradingagents.dataflows.trade_calendar import cn_today_str


def _serialize_datetime_utc(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat()


class UserContextInput(BaseModel):
    objective: Optional[str] = Field(None, description="用户目标动作，如建仓/加仓/减仓/止损/观察")
    risk_profile: Optional[str] = Field(None, description="风险偏好，如保守/平衡/激进")
    investment_horizon: Optional[str] = Field(None, description="持有周期，如短线/波段/中线")
    cash_available: Optional[float] = Field(None, description="可用资金")
    current_position: Optional[float] = Field(None, description="当前持仓数量")
    current_position_pct: Optional[float] = Field(None, description="当前仓位占比")
    average_cost: Optional[float] = Field(None, description="当前持仓成本")
    max_loss_pct: Optional[float] = Field(None, description="最大容忍亏损百分比")
    constraints: List[str] = Field(default_factory=list, description="用户的硬约束列表")
    user_notes: Optional[str] = Field(None, description="用户补充说明")


class AnalyzeRequest(UserContextInput):
    symbol: str = Field(default="", description="股票代码，如 600519.SH（当 query 包含代码时可省略）")
    trade_date: str = Field(default_factory=cn_today_str, description="交易日期 YYYY-MM-DD")
    # 优化：默认减少分析师数量以提升响应速度（核心3个：市场+新闻+基本面）
    selected_analysts: List[str] = Field(
        default_factory=lambda: ["market", "news", "fundamentals"]
    )
    config_overrides: Dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False
    # When set, triggers intent-driven analysis via streaming dual-horizon path
    query: Optional[str] = Field(default=None, description="自然语言查询，如：分析贵州茅台短线机会")
    horizons: List[str] = Field(default_factory=lambda: ["short"], description="分析周期列表，如 ['short'] 或 ['short','medium']")
    # Pre-parsed intent from _ai_extract_symbol_and_date (avoids second LLM call in _run_job)
    user_intent: Optional[Dict[str, Any]] = Field(default=None, description="预解析的用户意图，由 chat_completions 传入")


class AnalyzeResponse(BaseModel):
    job_id: str
    status: Literal["pending", "running", "completed", "failed"]
    created_at: str


class BatchScheduledTriggerJob(BaseModel):
    item_id: str
    job_id: str
    symbol: str
    name: str
    status: Literal["pending", "running", "completed", "failed"]
    created_at: str
    current_position: Optional[float] = None
    average_cost: Optional[float] = None
    waiting_ahead_count: Optional[int] = None
    scheduled_running_count: Optional[int] = None
    scheduled_concurrency_limit: Optional[int] = None


class BatchScheduledTriggerResponse(BaseModel):
    summary: Dict[str, int]
    jobs: List[BatchScheduledTriggerJob]


class JobStatusResponse(BaseModel):
    job_id: str
    status: Literal["pending", "running", "completed", "failed"]
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    symbol: str
    trade_date: str
    error: Optional[str] = None
    waiting_ahead_count: Optional[int] = None
    scheduled_running_count: Optional[int] = None
    scheduled_concurrency_limit: Optional[int] = None


class ChatMessage(BaseModel):
    role: str
    content: Any


class ChatCompletionRequest(UserContextInput):
    model: Optional[str] = "tradingagents-ashare"
    messages: List[ChatMessage]
    stream: bool = True
    # 优化：默认减少分析师数量以提升响应速度（核心3个：市场+新闻+基本面）
    selected_analysts: List[str] = Field(
        default_factory=lambda: ["market", "news", "fundamentals"]
    )
    config_overrides: Dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False


class KlineResponse(BaseModel):
    symbol: str
    start_date: str
    end_date: str
    candles: List[Dict[str, Any]]


# Report API Models
class ReportCreateRequest(BaseModel):
    symbol: str = Field(..., description="股票代码")
    trade_date: str = Field(..., description="交易日期 YYYY-MM-DD")
    decision: Optional[str] = Field(None, description="交易决策")
    result_data: Optional[Dict[str, Any]] = Field(None, description="完整分析结果")


class ReportResponse(BaseModel):
    id: str
    user_id: Optional[str]
    symbol: str
    name: Optional[str] = None
    trade_date: str
    status: Literal["pending", "running", "completed", "failed"] = "completed"
    error: Optional[str] = None
    decision: Optional[str]
    direction: Optional[str]
    confidence: Optional[int]
    target_price: Optional[float]
    stop_loss_price: Optional[float]
    risk_items: Optional[List[Dict[str, Any]]] = None
    key_metrics: Optional[List[Dict[str, Any]]] = None
    analyst_traces: Optional[List[Dict[str, Any]]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    waiting_ahead_count: Optional[int] = None
    scheduled_running_count: Optional[int] = None
    scheduled_concurrency_limit: Optional[int] = None

    model_config = {"from_attributes": True}

    @field_serializer("created_at", "updated_at", when_used="json")
    def serialize_report_datetimes(self, value: Optional[datetime]) -> Optional[str]:
        return _serialize_datetime_utc(value)


class ReportDetailResponse(ReportResponse):
    market_report: Optional[str]
    sentiment_report: Optional[str]
    news_report: Optional[str]
    fundamentals_report: Optional[str]
    macro_report: Optional[str]
    smart_money_report: Optional[str]
    volume_price_report: Optional[str]
    game_theory_report: Optional[str]
    investment_plan: Optional[str]
    trader_investment_plan: Optional[str]
    final_trade_decision: Optional[str]
    result_data: Optional[Dict[str, Any]]


class ReportListResponse(BaseModel):
    total: int
    reports: List[ReportResponse]


class ReportBatchDeleteRequest(BaseModel):
    report_ids: List[str] = Field(default_factory=list)


class ReportBatchDeleteResponse(BaseModel):
    deleted_ids: List[str]
    missing_ids: List[str]


class LatestReportsBySymbolsRequest(BaseModel):
    symbols: List[str] = Field(default_factory=list)


class LatestReportsBySymbolsResponse(BaseModel):
    reports: List[ReportResponse]


class PortfolioOverviewResponse(BaseModel):
    watchlist: List[dict]
    scheduled: List[dict]
    latest_reports: List[ReportResponse]
    portfolio_import: Optional[dict] = None


class WatchlistAddRequest(BaseModel):
    text: Optional[str] = None
    symbol: Optional[str] = None


class ScheduledBatchIdsRequest(BaseModel):
    item_ids: List[str] = Field(default_factory=list)


class ScheduledBatchUpdateRequest(BaseModel):
    item_ids: List[str] = Field(default_factory=list)
    is_active: Optional[bool] = None
    horizon: Optional[str] = None
    trigger_time: Optional[str] = None


class AnnouncementItemResponse(BaseModel):
    title: str
    detail: str


class AnnouncementResponse(BaseModel):
    id: str
    tag: Optional[str] = None
    title: str
    summary: Optional[str] = None
    published_at: str
    items: List[AnnouncementItemResponse]
    cta_label: Optional[str] = None
    cta_path: Optional[str] = None


class LatestAnnouncementResponse(BaseModel):
    announcement: Optional[AnnouncementResponse] = None


class UserResponse(BaseModel):
    id: str
    email: str
    created_at: Optional[datetime] = None
    last_login_at: Optional[datetime] = None
    email_report_enabled: bool = True

    model_config = {"from_attributes": True}

    @field_serializer("created_at", "last_login_at", when_used="json")
    def serialize_user_datetimes(self, value: Optional[datetime]) -> Optional[str]:
        return _serialize_datetime_utc(value)


class AuthRequestCodeRequest(BaseModel):
    email: str


class AuthVerifyCodeRequest(BaseModel):
    email: str
    code: str


class AuthVerifyCodeResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class UserRuntimeConfigResponse(BaseModel):
    llm_provider: str
    deep_think_llm: str
    quick_think_llm: str
    backend_url: str
    max_debate_rounds: int
    max_risk_discuss_rounds: int
    has_api_key: bool = False
    has_wecom_webhook: bool = False
    wecom_webhook_display: Optional[str] = None
    server_fallback_enabled: bool = True
    email_report_enabled: bool = True
    wecom_report_enabled: bool = True
    # 优化：默认减少分析师数量以提升响应速度（核心3个：市场+新闻+基本面）
    default_analysts: List[str] = Field(default_factory=lambda: ["market", "news", "fundamentals"])


class UserRuntimeConfigUpdateRequest(BaseModel):
    llm_provider: Optional[str] = None
    deep_think_llm: Optional[str] = None
    quick_think_llm: Optional[str] = None
    backend_url: Optional[str] = None
    max_debate_rounds: Optional[int] = None
    max_risk_discuss_rounds: Optional[int] = None
    email_report_enabled: Optional[bool] = None
    wecom_report_enabled: Optional[bool] = None
    api_key: Optional[str] = None
    wecom_webhook_url: Optional[str] = None
    clear_api_key: bool = False
    clear_wecom_webhook: bool = False
    warmup: bool = True
    force_warmup: bool = False
    default_analysts: Optional[List[str]] = None


class UserRuntimeWarmupRequest(UserRuntimeConfigUpdateRequest):
    prompt: str = "你好"


class RuntimeWarmupResult(BaseModel):
    model: str
    targets: List[str] = Field(default_factory=list)
    content: Optional[str] = None
    error: Optional[str] = None


class UserRuntimeWarmupResponse(BaseModel):
    prompt: str
    results: List[RuntimeWarmupResult]


class WecomWebhookWarmupRequest(BaseModel):
    wecom_webhook_url: Optional[str] = None
    content: Optional[str] = None


class WecomWebhookWarmupResponse(BaseModel):
    sent: bool = True
    message: str
    webhook_display: Optional[str] = None


class PortfolioPositionItem(BaseModel):
    symbol: str = Field(..., description="股票代码，如 600519.SH 或 600519")
    name: Optional[str] = Field(None, description="股票名称")
    current_position: Optional[float] = Field(None, description="持仓数量")
    available_position: Optional[float] = Field(None, description="可用数量")
    average_cost: Optional[float] = Field(None, description="成本价")
    market_value: Optional[float] = Field(None, description="市值")
    current_position_pct: Optional[float] = Field(None, description="仓位占比 %")


class PortfolioImportSyncRequest(BaseModel):
    positions: List[PortfolioPositionItem] = Field(..., description="持仓列表")
    source: str = Field("manual", description="持仓来源标识")
    auto_apply_scheduled: bool = Field(True, description="是否自动将持仓股票加入定时任务")


class UserTokenResponse(BaseModel):
    id: str
    name: str
    token: str
    token_hint: Optional[str] = None
    last_used_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("created_at", "last_used_at", when_used="json")
    def serialize_token_datetimes(self, value: Optional[datetime]) -> Optional[str]:
        return _serialize_datetime_utc(value)


class UserTokenListItem(BaseModel):
    """Token info for list endpoint — never exposes the full token."""
    id: str
    name: str
    token_hint: Optional[str] = None
    last_used_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("created_at", "last_used_at", when_used="json")
    def serialize_token_datetimes(self, value: Optional[datetime]) -> Optional[str]:
        return _serialize_datetime_utc(value)


class UserTokenCreateRequest(BaseModel):
    name: str


# Backtest / sponsors / feedback models (used by misc.py and feedback.py routers)
class BacktestRequest(BaseModel):
    symbol: str
    start_date: str
    end_date: str
    selected_analysts: List[str] = ["market", "news", "fundamentals", "sentiment"]
    hold_days: int = 5
    sample_interval: int = 7
    config_overrides: Optional[Dict[str, Any]] = None


class SponsorItem(BaseModel):
    id: str
    sponsor_type: str
    name: str
    github: Optional[str] = None
    avatar: Optional[str] = None
    email: Optional[str] = None
    provider: Optional[str] = None
    date: str
    # NOTE: amount is intentionally excluded from the public API


class SponsorsResponse(BaseModel):
    money: List[SponsorItem]
    token: List[SponsorItem]


class FeedbackCreateRequest(BaseModel):
    subject: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1, max_length=5000)


class FeedbackItem(BaseModel):
    id: str
    user_email: str
    subject: str
    content: str
    admin_reply: Optional[str] = None
    replied_at: Optional[datetime] = None
    is_read: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_serializer("replied_at", "created_at", "updated_at")
    def serialize_dt(self, v: Optional[datetime], _info: Any) -> Optional[str]:
        return v.isoformat() if v else None


class FeedbackListResponse(BaseModel):
    total: int
    feedbacks: List[FeedbackItem]


class FeedbackUnreadResponse(BaseModel):
    unread_count: int
