"""User-context and scheduled-task helpers.

Extracted from ``api/main.py``.  All symbols are re-exported from
``api.main`` so existing ``from api.main import ...`` imports keep working.

Functions that touch ``api.main`` globals (``get_db_ctx`` / ``_run_job``) or
that tests patch on ``api.main`` resolve them lazily at call time.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from api.analysis_runner import _extract_message_text
from api.job_runtime import _emit_job_event
from api.schemas import AnalyzeRequest, ChatMessage, UserContextInput
from api.services import auth_service, portfolio_import_service, scheduled_service
from api.stock_map import _normalize_symbol, _search_cn_stock_by_name
from tradingagents.agents.utils.context_utils import USER_CONTEXT_KEYS, normalize_user_context

logger = logging.getLogger(__name__)


# ── Scheduled-task helpers ───────────────────────────────────────────────────

def _resolve_scheduled_trade_date(trade_date: str) -> str:
    """Use the requested trading day, or fall back to the latest CN trading day."""
    from tradingagents.dataflows.trade_calendar import is_cn_trading_day, previous_cn_trading_day

    return trade_date if is_cn_trading_day(trade_date) else previous_cn_trading_day(trade_date)


def _build_scheduled_analyze_request(
    db: Session,
    user_id: str,
    symbol: str,
    horizon: str,
    trade_date: str,
    scheduled_user_context: Optional[Dict[str, Any]] = None,
) -> "AnalyzeRequest":
    scheduled_user_context = scheduled_user_context or _build_imported_user_context(db, user_id, symbol)
    # Read user's saved analyst selection from DB
    user_cfg = auth_service.get_user_llm_config(db, user_id)
    selected = None
    if user_cfg and user_cfg.default_analysts:
        try:
            selected = json.loads(user_cfg.default_analysts)
        except Exception as e:
            logger.debug(f"failed to parse default_analysts config: {e}")
    req = AnalyzeRequest(
        symbol=symbol,
        trade_date=trade_date,
        horizons=[horizon],
        query=f"定时分析 {symbol}",
        user_intent={
            "ticker": symbol,
            "horizons": [horizon],
            "focus_areas": [],
            "specific_questions": [],
            "user_context": scheduled_user_context,
        },
        objective=scheduled_user_context.get("objective"),
        current_position=scheduled_user_context.get("current_position"),
        current_position_pct=scheduled_user_context.get("current_position_pct"),
        average_cost=scheduled_user_context.get("average_cost"),
        user_notes=scheduled_user_context.get("user_notes"),
    )
    if selected:
        req.selected_analysts = selected
    return req


async def _run_manual_trigger(
    task: dict,
    requested_trade_date: str,
    job_id: str,
) -> None:
    """Execute a manual-trigger analysis (no scheduler concurrency control).

    Used by the /v1/scheduled/{id}/trigger and /v1/scheduled/batch/trigger
    endpoints. Calls _run_job directly then records the test result.
    """
    import traceback

    # Resolve via api.main so patches of ``api.main._run_job`` / ``get_db_ctx`` apply.
    import api.main as _main

    task_id = task["id"]
    user_id = task["user_id"]
    symbol = task["symbol"]
    horizon = task.get("horizon") or "short"

    actual_trade_date = _resolve_scheduled_trade_date(requested_trade_date)
    logger.info(
        f"[Manual Trigger] {symbol} trade_date={actual_trade_date} (requested={requested_trade_date})"
    )

    try:
        with _main.get_db_ctx() as db:
            scheduled_user_context = task.get("manual_user_context") or _build_imported_user_context(
                db, user_id, symbol
            )
            req = _build_scheduled_analyze_request(
                db=db,
                user_id=user_id,
                symbol=symbol,
                horizon=horizon,
                trade_date=actual_trade_date,
                scheduled_user_context=scheduled_user_context,
            )

        await _main._run_job(job_id, req, False, True, user_id, "scheduled_manual")
        job_state = _main._get_job(job_id)
        if job_state.get("status") == "failed":
            raise RuntimeError(job_state.get("error") or f"manual trigger job {job_id} failed")
        with _main.get_db_ctx() as db:
            scheduled_service.record_manual_test_result(db, task_id, "success", report_id=job_id)
        logger.info(f"[Manual Trigger] Completed {symbol}")
    except Exception as e:
        logger.error(f"[Manual Trigger] Failed {symbol}: {e}\n{traceback.format_exc()}")
        with _main.get_db_ctx() as db:
            scheduled_service.record_manual_test_result(db, task_id, "failed")


def _annotate_scheduled_with_imported_context(items: List[dict], db: Session, user_id: str) -> List[dict]:
    imported_map: Dict[str, Dict[str, Any]] = {}
    for item in portfolio_import_service.list_imported_positions(db, user_id):
        imported_map[item["symbol"]] = item
    for item in items:
        imported = imported_map.get(item["symbol"])
        item["has_imported_context"] = imported is not None
        item["imported_current_position"] = imported.get("current_position") if imported else None
        item["imported_average_cost"] = imported.get("average_cost") if imported else None
        item["imported_trade_points_count"] = imported.get("trade_points_count") if imported else 0
    return items


def _merge_imported_user_context(*contexts: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    note_parts: List[str] = []
    for ctx in contexts:
        if not ctx:
            continue
        for key, value in ctx.items():
            if key == "user_notes":
                if value:
                    note_parts.append(str(value).strip())
                continue
            if value is not None:
                merged[key] = value
    if note_parts:
        merged["user_notes"] = "\n\n".join(part for part in note_parts if part)
    return normalize_user_context(merged)


def _build_imported_user_context(db: Session, user_id: str, symbol: str) -> Dict[str, Any]:
    context = portfolio_import_service.build_scheduled_user_context(db, user_id, symbol)
    return _merge_imported_user_context(context)


def _build_manual_imported_user_context(db: Session, user_id: str, symbol: str) -> Dict[str, Any]:
    """Build imported position context for manual/ad-hoc analysis runs."""
    return _build_imported_user_context(db, user_id, symbol)


def _extract_scheduled_update_kwargs(body: dict) -> dict:
    kwargs = {}
    if "is_active" in body:
        kwargs["is_active"] = bool(body["is_active"])
    if "horizon" in body:
        kwargs["horizon"] = body["horizon"]
    if "trigger_time" in body:
        kwargs["trigger_time"] = body["trigger_time"]
    return kwargs


# ── User-context helpers ─────────────────────────────────────────────────────

# All analysts always run — each uses its own natural time window
# (technical/funds → short, fundamentals/macro → medium)
def _get_horizon_analysts(horizon: str, available: List[str]) -> List[str]:
    """Return all available analysts regardless of horizon."""
    return list(available)


def _extract_request_user_context(request: UserContextInput) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for key in USER_CONTEXT_KEYS:
        value = getattr(request, key, None)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if key == "constraints" and not value:
            continue
        payload[key] = value
    return payload


def _merge_user_context_payload(
    explicit_context: Dict[str, Any],
    inferred_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    merged = normalize_user_context(inferred_context or {})
    merged.update(normalize_user_context(explicit_context or {}))
    return merged


def _compose_analysis_user_context(
    db: Session,
    user_id: str,
    symbol: str,
    *,
    explicit_context: Optional[Dict[str, Any]] = None,
    inferred_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    imported_context = _build_manual_imported_user_context(db, user_id, symbol)
    merged_with_imported = _merge_user_context_payload(inferred_context or {}, imported_context)
    return _merge_user_context_payload(explicit_context or {}, merged_with_imported)


def _apply_user_context_to_request(request: "AnalyzeRequest", user_context: Dict[str, Any]) -> "AnalyzeRequest":
    request.objective = user_context.get("objective")
    request.risk_profile = user_context.get("risk_profile")
    request.investment_horizon = user_context.get("investment_horizon")
    request.cash_available = user_context.get("cash_available")
    request.current_position = user_context.get("current_position")
    request.current_position_pct = user_context.get("current_position_pct")
    request.average_cost = user_context.get("average_cost")
    request.max_loss_pct = user_context.get("max_loss_pct")
    request.constraints = user_context.get("constraints", [])
    request.user_notes = user_context.get("user_notes")
    return request


# ── Chat text / symbol extraction ────────────────────────────────────────────

def _extract_chat_text(messages: List[ChatMessage]) -> str:
    if not messages:
        return ""
    last = messages[-1]
    return _extract_message_text(last.content)


def _extract_symbol_and_date(text: str) -> tuple[Optional[str], Optional[str]]:
    # Date extraction (flexible boundaries)
    date_match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    date = date_match.group(0) if date_match else None

    # Priority 1: Hong Kong stocks (e.g. 0700.HK, 9988.HK)
    hk_match = re.search(r"(\d{4,5}\.HK)", text, re.IGNORECASE)
    if hk_match:
        return hk_match.group(1).upper(), date

    # Priority 2: A-Share 6-digit code (even if stuck to Chinese characters)
    sym_match = re.search(r"(\d{6}(?:\.(?:SH|SZ|SS))?)", text, re.IGNORECASE)
    if sym_match:
        return _normalize_symbol(sym_match.group(1)), date

    # Priority 3: US Stocks or other Tickers (use boundaries for letters to avoid partial words)
    us_match = re.search(r"\b([A-Z]{1,6}(?:\.[A-Z]{1,3})?)\b", text.upper())
    if us_match:
        return us_match.group(1), date

    return None, date


async def _ai_extract_symbol_and_date_streaming(
    text: str, config: Dict[str, Any], job_id: str
) -> tuple[Optional[str], Optional[str], List[str], List[str], List[str], Dict[str, Any]]:
    """
    Async streaming version of _ai_extract_symbol_and_date.
    Emits agent.token events so the frontend can show streaming output during extraction.
    """
    import asyncio
    from tradingagents.llm_clients.factory import create_llm_client
    import json as _json

    today = datetime.now().strftime("%Y-%m-%d")
    llm_name: Optional[str] = None
    llm_date: Optional[str] = None
    llm_horizons: List[str] = ["short"]
    llm_focus_areas: List[str] = []
    llm_specific_questions: List[str] = []
    llm_user_context: Dict[str, Any] = {}

    try:
        client = create_llm_client(
            provider=config.get("llm_provider", "openai"),
            model=config.get("quick_think_llm"),
            base_url=config.get("backend_url"),
            api_key=config.get("api_key"),
        )
        prompt = f"""你是金融数据助手。从用户消息中提取以下字段并以 JSON 输出。

字段说明：
- stock_name：用户提到的公司名称或股票代码原文（如"华盛天成"、"贵州茅台"、"600519"、"0700.HK"、"AAPL"）；美股/港股直接填 ticker。
- date：YYYY-MM-DD 格式。今天是 {today}，如未提及则填今天。
- horizons：分析周期，只能选一个：
  * 用户明确提到"中线/中期/几个月/季度/长期/趋势投资"→ ["medium"]
  * 其他所有情况（含未提及）→ ["short"]
- focus_areas：用户关注的分析维度关键词列表，如 ["技术面", "资金面", "业绩"]，未提及则 []。
- specific_questions：用户提出的具体问题列表，如 ["近期有无催化剂？", "主力是否出货？"]，未提及则 []。
- user_context：从自然语言中提取的账户与约束对象。若未提及返回 {{}}。可包含：
  * objective：建仓 / 加仓 / 减仓 / 止损 / 观察 / 持有处理
  * risk_profile：保守 / 平衡 / 激进
  * investment_horizon：短线 / 波段 / 中线 / 长期
  * cash_available / current_position / current_position_pct / average_cost / max_loss_pct：数字
  * constraints：字符串数组
  * user_notes：仅保留重要但未能结构化归类的信息

仅输出 JSON，不要任何其他文字：
{{"stock_name": "...", "date": "YYYY-MM-DD", "horizons": ["short"], "focus_areas": [], "specific_questions": [], "user_context": {{}}}}

如果无法识别股票标的：{{"stock_name": null, "date": null, "horizons": ["short"], "focus_areas": [], "specific_questions": [], "user_context": {{}}}}

用户消息："{text}"
"""
        llm = client.get_llm()
        logger.info(f"[LLM Debug] Streaming StockExtract with model: {getattr(llm, 'model_name', 'unknown')}")

        full_content = ""
        try:
            # 添加 60 秒超时，防止 LLM 调用挂起
            async def _stream_extract():
                nonlocal full_content
                async for chunk in llm.astream(prompt):
                    token = chunk.content if hasattr(chunk, "content") else str(chunk)
                    full_content += token
                    if token:
                        _emit_job_event(job_id, "agent.token", {
                            "agent": "意图解析",
                            "report": "stock_extract",
                            "token": token,
                        })

            await asyncio.wait_for(_stream_extract(), timeout=60.0)
        except asyncio.TimeoutError:
            logger.info(f"[StockExtract] LLM streaming timed out after 60s")
        except Exception as e:
            logger.info(f"[StockExtract streaming] LLM failed: {e}")

        logger.info(f"[LLM Debug] StockExtract response: {full_content[:200]}")
        m = re.search(r"\{.*\}", full_content, re.DOTALL)
        if m:
            data = _json.loads(m.group(0))
            llm_name = (data.get("stock_name") or "").strip() or None
            llm_date = data.get("date") or today
            llm_horizons = data.get("horizons") or ["short"]
            llm_focus_areas = data.get("focus_areas") or []
            llm_specific_questions = data.get("specific_questions") or []
            llm_user_context = normalize_user_context(data.get("user_context") or {})
    except Exception as e:
        logger.info(f"[StockExtract streaming] LLM failed: {e}")

    if not llm_name:
        return None, None, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context

    logger.info(f"[StockExtract] extracted name='{llm_name}', date={llm_date}, horizons={llm_horizons}")
    # Support A-share 6-digit, HK stocks (e.g. 0700.HK), and US tickers (e.g. AAPL)
    if re.match(r"^\d{6}$", llm_name) or re.match(r"^\d{4,5}\.HK$", llm_name, re.IGNORECASE) or re.match(r"^[A-Za-z]{1,6}(\.[A-Za-z]+)?$", llm_name):
        symbol = _normalize_symbol(llm_name)
        return symbol or None, llm_date, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context

    local_code = await asyncio.to_thread(_search_cn_stock_by_name, llm_name)
    if local_code:
        return local_code, llm_date, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context

    fallback = _normalize_symbol(llm_name)
    if fallback:
        return fallback, llm_date, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context

    return None, llm_date, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context


def _ai_extract_symbol_and_date(
    text: str, config: Dict[str, Any]
) -> tuple[Optional[str], Optional[str], List[str], List[str], List[str], Dict[str, Any]]:
    """
    Single-LLM extraction: stock name, date, horizons, focus_areas, specific_questions.
    Then resolves the stock name to an authoritative code via akshare.
    Returns (symbol, date, horizons, focus_areas, specific_questions, inferred_user_context).
    """
    from tradingagents.llm_clients.factory import create_llm_client
    import json as _json

    today = datetime.now().strftime("%Y-%m-%d")

    llm_name: Optional[str] = None
    llm_date: Optional[str] = None
    llm_horizons: List[str] = ["short"]
    llm_focus_areas: List[str] = []
    llm_specific_questions: List[str] = []
    llm_user_context: Dict[str, Any] = {}
    try:
        client = create_llm_client(
            provider=config.get("llm_provider", "openai"),
            model=config.get("quick_think_llm"),
            base_url=config.get("backend_url"),
            api_key=config.get("api_key"),
        )
        prompt = f"""你是金融数据助手。从用户消息中提取以下字段并以 JSON 输出。

字段说明：
- stock_name：用户提到的公司名称或股票代码原文（如"华盛天成"、"贵州茅台"、"600519"、"0700.HK"、"AAPL"）；美股/港股直接填 ticker。
- date：YYYY-MM-DD 格式。今天是 {today}，如未提及则填今天。
- horizons：分析周期，只能选一个：
  * 用户明确提到"中线/中期/几个月/季度/长期/趋势投资"→ ["medium"]
  * 其他所有情况（含未提及）→ ["short"]
- focus_areas：用户关注的分析维度关键词列表，如 ["技术面", "资金面", "业绩"]，未提及则 []。
- specific_questions：用户提出的具体问题列表，如 ["近期有无催化剂？", "主力是否出货？"]，未提及则 []。
- user_context：从自然语言中提取的账户与约束对象。若未提及返回 {{}}。可包含：
  * objective：建仓 / 加仓 / 减仓 / 止损 / 观察 / 持有处理
  * risk_profile：保守 / 平衡 / 激进
  * investment_horizon：短线 / 波段 / 中线 / 长期
  * cash_available / current_position / current_position_pct / average_cost / max_loss_pct：数字
  * constraints：字符串数组
  * user_notes：仅保留重要但未能结构化归类的信息

仅输出 JSON，不要任何其他文字：
{{"stock_name": "...", "date": "YYYY-MM-DD", "horizons": ["short"], "focus_areas": [], "specific_questions": [], "user_context": {{}}}}

如果无法识别股票标的：{{"stock_name": null, "date": null, "horizons": ["short"], "focus_areas": [], "specific_questions": [], "user_context": {{}}}}

用户消息："{text}"
"""
        llm = client.get_llm()

        # 调试日志：打印请求参数
        target_url = getattr(llm, 'openai_api_base', 'default')
        logger.info(f"[LLM Debug] Requesting StockExtract with model: {getattr(llm, 'model_name', 'unknown')} at {target_url}")
        logger.info(f"[LLM Debug] Prompt: {prompt[:500]}...")

        response = llm.invoke(prompt)
        raw = response if isinstance(response, str) else getattr(response, "content", str(response))

        # 调试日志：打印原始响应
        logger.info(f"[LLM Debug] Raw Response: {raw}")

        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            data = _json.loads(m.group(0))
            llm_name = (data.get("stock_name") or "").strip() or None
            llm_date = data.get("date") or today
            llm_horizons = data.get("horizons") or ["short"]
            llm_focus_areas = data.get("focus_areas") or []
            llm_specific_questions = data.get("specific_questions") or []
            llm_user_context = normalize_user_context(data.get("user_context") or {})
    except Exception as e:
        logger.info(f"[StockExtract] LLM failed: {e}")

    if not llm_name:
        logger.info(f"[StockExtract] LLM returned no stock name for: '{text[:40]}'")
        return None, None, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context

    logger.info(f"[StockExtract] LLM extracted name='{llm_name}', date={llm_date}, horizons={llm_horizons}")

    # ── Step 2: If looks like a direct code (digits / letters), normalize it ──
    # Support A-share 6-digit, HK stocks (e.g. 0700.HK), and US tickers (e.g. AAPL)
    if re.match(r"^\d{6}$", llm_name) or re.match(r"^\d{4,5}\.HK$", llm_name, re.IGNORECASE) or re.match(r"^[A-Za-z]{1,6}(\.[A-Za-z]+)?$", llm_name):
        symbol = _normalize_symbol(llm_name)
        logger.info(f"[StockExtract] Direct code: {symbol}")
        return symbol or None, llm_date, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context

    # ── Step 3: Search akshare A-share name database ──────────────────────────
    local_code = _search_cn_stock_by_name(llm_name)
    if local_code:
        logger.info(f"[StockExtract] akshare match: '{llm_name}' → {local_code}")
        return local_code, llm_date, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context

    # ── Step 4: Last resort — treat LLM name as a raw code ────────────────────
    fallback = _normalize_symbol(llm_name)
    if fallback:
        logger.info(f"[StockExtract] Fallback normalize: '{llm_name}' → {fallback}")
        return fallback, llm_date, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context

    logger.info(f"[StockExtract] Could not resolve '{llm_name}' to a stock code")
    return None, llm_date, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context
