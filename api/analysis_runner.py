"""Analysis job runner: ``_run_job`` / ``_run_job_inner`` and helpers.

Extracted from ``api/main.py``.  All symbols are re-exported from
``api.main`` so existing ``from api.main import ...`` imports keep working.
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from typing import Any, Dict, List, Optional

from api.database import get_db_ctx
from api.runtime_config import _build_runtime_config
from api.schemas import AnalyzeRequest
from api.services import report_service
from api.stock_map import _normalize_symbol
from api.job_runtime import (
    ANALYST_AGENT_NAMES,
    ANALYST_ORDER,
    ANALYST_REPORT_MAP,
    AgentProgressTracker,
    _JOB_TIMEOUT,
    _emit_job_event,
    _set_job,
    _utcnow_iso,
)
from tradingagents.agents.utils.agent_states import current_tracker_var
from tradingagents.graph.intent_parser import parse_intent as _parse_intent
from tradingagents.graph.trading_graph import TradingAgentsGraph

logger = logging.getLogger(__name__)
_log = logger.info


# Lazy-imported symbols that live in api.main (avoid circular import at load time).
def _main_deps():
    import api.main as _main
    return (
        _main._extract_request_user_context,
        _main._merge_user_context_payload,
        _main._get_horizon_analysts,
        _main._shared_data_collector,
    )


# ── Result / message helpers ─────────────────────────────────────────────────

def _build_result_payload(final_state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "symbol": final_state.get("company_of_interest"),
        "trade_date": final_state.get("trade_date"),
        "direction": None,
        "instrument_context": final_state.get("instrument_context"),
        "market_context": final_state.get("market_context"),
        "user_context": final_state.get("user_context"),
        "workflow_context": final_state.get("workflow_context"),
        "market_report": final_state.get("market_report"),
        "sentiment_report": final_state.get("sentiment_report"),
        "news_report": final_state.get("news_report"),
        "fundamentals_report": final_state.get("fundamentals_report"),
        "macro_report": final_state.get("macro_report"),
        "smart_money_report": final_state.get("smart_money_report"),
        "volume_price_report": final_state.get("volume_price_report"),
        "game_theory_report": final_state.get("game_theory_report"),
        "game_theory_signals": final_state.get("game_theory_signals"),
        "analyst_traces": final_state.get("analyst_traces"),
        "investment_plan": final_state.get("investment_plan"),
        "trader_investment_plan": final_state.get("trader_investment_plan"),
        "risk_feedback_state": final_state.get("risk_feedback_state"),
        "final_trade_decision": final_state.get("final_trade_decision"),
    }


def _extract_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()
    return str(content)


def _generate_tool_description(tool_name: str, tool_args: Dict[str, Any]) -> str:
    """生成工具调用的可读描述"""
    if tool_name == "get_indicators":
        indicator = tool_args.get("indicator")
        if isinstance(indicator, str) and indicator:
            indicator_map = {
                "close_50_sma": "50日均线",
                "close_200_sma": "200日均线",
                "close_10_ema": "10日EMA",
                "close_20_ema": "20日EMA",
                "rsi": "RSI",
                "macd": "MACD",
                "boll": "布林中轨",
                "boll_ub": "布林上轨",
                "boll_lb": "布林下轨",
                "atr": "ATR波动率",
                "vwma": "VWMA量价均线",
                "obv": "OBV能量潮",
            }
            return f"计算 {indicator_map.get(indicator, indicator)}"
        return "获取技术指标"
    elif tool_name == "get_stock_data":
        return "获取股票历史数据"
    elif tool_name == "get_fundamentals":
        metrics = tool_args.get("metrics", [])
        if metrics:
            return f"获取 {', '.join(metrics[:2])}{' 等' if len(metrics) > 2 else ''} 基本面数据"
        return "获取基本面数据"
    elif tool_name == "get_income_statement":
        return "获取利润表"
    elif tool_name == "get_balance_sheet":
        return "获取资产负债表"
    elif tool_name == "get_cash_flow":
        return "获取现金流量表"
    elif tool_name == "get_news":
        return "获取相关新闻"
    elif tool_name == "get_social_sentiment":
        return "获取舆情数据"
    return f"调用 {tool_name}"


# ── Job entry point ──────────────────────────────────────────────────────────

async def _run_job(
    job_id: str,
    request: AnalyzeRequest,
    stream_events: bool = False,
    save_report: bool = True,
    user_id: Optional[str] = None,
    request_source: str = "api",
) -> None:
    # 用 asyncio.Task + sleep 竞速代替 wait_for，避免 cancel 卡在 to_thread 导致
    # semaphore 永远不释放的问题。超时后标记失败但不 cancel 内部协程（让线程自然结束）。
    inner_task = asyncio.create_task(
        _run_job_inner(job_id, request, stream_events, save_report, user_id, request_source)
    )
    done, _ = await asyncio.wait({inner_task}, timeout=_JOB_TIMEOUT)
    if inner_task in done:
        # 正常完成（可能成功也可能异常）
        if not inner_task.cancelled() and inner_task.exception():
            _log(f"[Job {job_id}] failed: {inner_task.exception()}")
        return
    # 超时：标记失败，但不 cancel 内部 task（避免 cancel 卡住）
    err_msg = f"任务超时（超过 {_JOB_TIMEOUT} 秒），已自动终止"
    _log(f"[Job {job_id}] {err_msg}")
    _set_job(job_id, status="failed", error=err_msg, finished_at=_utcnow_iso())
    # 注意：不能用 asyncio.to_thread 写 DB，因为线程池可能被僵尸任务占满导致死锁。
    # 用同步方式直接写，SQLite 的写入足够快不会阻塞事件循环。
    try:
        with get_db_ctx() as db:
            report_service.mark_report_failed(db, job_id, err_msg)
    except Exception as e:
        logger.warning(f"[Job {job_id}] failed to mark report as failed in DB: {e}")
    _emit_job_event(job_id, "job.failed", {"job_id": job_id, "error": err_msg})


async def _run_job_inner(
    job_id: str,
    request: AnalyzeRequest,
    stream_events: bool = False,
    save_report: bool = True,
    user_id: Optional[str] = None,
    request_source: str = "api",
) -> None:
    (
        _extract_request_user_context,
        _merge_user_context_payload,
        _get_horizon_analysts,
        _shared_data_collector,
    ) = _main_deps()

    job_start_t = time.time()
    # Normalize for logic but keep original for display
    display_name = request.symbol
    normalized_symbol = _normalize_symbol(request.symbol)

    # ── Step 0: Initialize report in DB (short-lived session) ──
    def _init_and_configure():
        with get_db_ctx() as db:
            try:
                report_service.init_report(
                    db=db,
                    report_id=job_id,
                    symbol=normalized_symbol,
                    trade_date=request.trade_date,
                    user_id=user_id,
                )
                report_service.update_report_partial(db, job_id, status="running")
                db.commit()
            except Exception as e:
                _log(f"CRITICAL: Failed to initialize report in DB: {e}")
        return _build_runtime_config(request.config_overrides, user_id=user_id)

    config = await asyncio.to_thread(_init_and_configure)

    _set_job(job_id, status="running", started_at=_utcnow_iso(), symbol=normalized_symbol)

    _emit_job_event(
        job_id,
        "job.running",
        {
            "job_id": job_id,
            "symbol": normalized_symbol,
            "display_name": display_name,
            "trade_date": request.trade_date
        },
    )
    # Ensure request object uses the normalized symbol for internal logic
    request.symbol = normalized_symbol
    user_context_payload = _extract_request_user_context(request)
    tracker = AgentProgressTracker(request.selected_analysts, job_id)
    _emit_job_event(job_id, "agent.snapshot", tracker.snapshot())

    try:
        if request.dry_run:
            result = {
                "mode": "dry_run",
                "symbol": request.symbol,
                "trade_date": request.trade_date,
                "selected_analysts": request.selected_analysts,
                "user_context": user_context_payload,
                "llm_provider": config.get("llm_provider"),
                "data_vendors": config.get("data_vendors"),
            }
            _set_job(
                job_id,
                status="completed",
                result=result,
                decision="DRY_RUN",
                finished_at=_utcnow_iso(),
            )
            _emit_job_event(
                job_id,
                "job.completed",
                {"job_id": job_id, "decision": "DRY_RUN", "result": result},
            )
            return

        _log(f"[Checkpoint] Job {job_id}: initializing TradingAgentsGraph for {normalized_symbol}")
        _shared_data_collector.ref(request.symbol, request.trade_date)
        graph = TradingAgentsGraph(
            selected_analysts=request.selected_analysts,
            debug=False,
            config=config,
            data_collector=_shared_data_collector,
        )
        _log(f"[Checkpoint] Job {job_id}: TradingAgentsGraph initialized (selected_analysts={request.selected_analysts})")
        final_state: Optional[Dict[str, Any]] = None

        # 强制单周期：多个 horizon 时只取第一个，避免 dual-horizon 双倍开销
        if not request.horizons:
            request.horizons = ["short"]
        elif len(request.horizons) > 1:
            request.horizons = [request.horizons[0]]

        # ── Dual-horizon intent-driven path ──────────────────────────────────
        if request.query:
            # 1. 组装用户意图
            intent_start_t = time.time()
            ticker = request.symbol or display_name

            # 优先使用已由 chat_completions 预解析的 intent（单次 LLM），避免二次调用
            if request.user_intent:
                user_intent = dict(request.user_intent)
                user_intent["ticker"] = ticker
                user_intent["horizons"] = request.horizons
            else:
                # 直接 POST /v1/analyze 时的兜底（无预解析 intent）
                _log(f"[Checkpoint] Job {job_id}: parsing intent from query (no pre-parsed intent)")
                try:
                    user_intent = await asyncio.to_thread(_parse_intent, request.query, graph.quick_thinking_llm, fallback_ticker=ticker)
                except Exception as _intent_exc:
                    logger.error(f"[Checkpoint] Job {job_id}: intent parsing failed: {_intent_exc}\n{traceback.format_exc()}")
                    raise
                if not request.horizons:
                    request.horizons = user_intent["horizons"]
                user_intent["horizons"] = request.horizons
            _log(f"[Timer] Intent Parsing took {time.time() - intent_start_t:.2f}s")

            inferred_user_context = user_intent.get("user_context") or {}
            user_context_payload = _merge_user_context_payload(
                user_context_payload,
                inferred_user_context,
            )
            user_intent["user_context"] = user_context_payload

            # Use normalized ticker from intent parser if available
            ticker = user_intent.get("ticker") or ticker

            # 2. 一次性采集数据，短线/中线共用缓存
            lookback_label = "14天关键行情" if request.horizons == ["short"] else "90天全量行情、财务、新闻、资金"
            _emit_job_event(job_id, "agent.tool_call", {
                "agent": "数据采集", "tool": "data_collector",
                "description": f"预加载 {ticker} 近{lookback_label}数据…",
            })
            _log(f"[Checkpoint] Job {job_id}: starting data collection for {ticker} {request.trade_date} (horizons={request.horizons})")
            _log(f"[DualHorizon] Collecting data for {ticker} {request.trade_date} (horizons={request.horizons})…")
            collect_start_t = time.time()
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(graph.data_collector.collect, ticker, request.trade_date, horizons=request.horizons),
                    timeout=60.0  # 1分钟数据采集超时（生产环境优化）
                )
            except asyncio.TimeoutError:
                _log(f"[Checkpoint] Job {job_id}: data collection timed out after 120s, continuing with partial data")
            except Exception as _collect_exc:
                logger.error(f"[Checkpoint] Job {job_id}: data collection FAILED after {time.time() - collect_start_t:.2f}s: {_collect_exc}\n{traceback.format_exc()}")
                raise
            _log(f"[Checkpoint] Job {job_id}: data collection completed in {time.time() - collect_start_t:.2f}s")
            _log(f"[Timer] Data Collection step in _run_job took {time.time() - collect_start_t:.2f}s")

            _emit_job_event(job_id, "agent.tool_call", {
                "agent": "数据采集", "tool": "data_collector",
                "description": "数据采集完成，开始多维度分析",
            })

            report_keys = (
                "market_report", "sentiment_report", "news_report", "fundamentals_report",
                "macro_report", "smart_money_report", "volume_price_report",
                "investment_plan", "trader_investment_plan", "final_trade_decision",
            )

            horizon_states: Dict[str, Any] = {}

            async def _process_horizon(horizon: str):
                """Async helper to run analysis for a single horizon."""
                horizon_start_t = time.time()
                _log(f"[Checkpoint] Job {job_id}: starting horizon '{horizon}' analysis for {ticker}")
                # 根据周期过滤 analyst，共享已采集的数据缓存
                horizon_analysts = _get_horizon_analysts(horizon, request.selected_analysts)
                _log(f"[Checkpoint] Job {job_id}: horizon '{horizon}' analysts={horizon_analysts}")
                horizon_graph = TradingAgentsGraph(
                    selected_analysts=horizon_analysts,
                    debug=False,
                    config=config,
                    data_collector=graph.data_collector,
                )

                horizon_label = "短线" if horizon == "short" else "中线"
                _emit_job_event(job_id, "agent.horizon_start", {
                    "horizon": horizon, "label": horizon_label,
                })
                # 每轮重置 tracker，前端进度条重新走一遍
                h_tracker = AgentProgressTracker(horizon_analysts, job_id, horizon=horizon)
                _emit_job_event(job_id, "agent.snapshot", h_tracker.snapshot())
                # 告知前端本轮参与的 analyst 即将开始
                for analyst_key in ANALYST_ORDER:
                    if analyst_key in horizon_analysts:
                        aname = ANALYST_AGENT_NAMES[analyst_key]
                        h_tracker._set_status(aname, "in_progress")
                        h_tracker._emit_writing_status(aname, ANALYST_REPORT_MAP[analyst_key])

                h_args = horizon_graph.propagator.get_graph_args()

                # Use thread_id for LangGraph checkpointer persistence
                if "config" not in h_args:
                    h_args["config"] = {}
                h_args["config"]["configurable"] = {"thread_id": f"{job_id}_{horizon}"}

                init_state = horizon_graph.propagator.create_initial_state(
                    ticker, request.trade_date,
                    user_context=user_context_payload,
                    selected_analysts=horizon_analysts,
                    request_source=request_source,
                    user_intent=user_intent, horizon=horizon,
                )
                last_report: Dict[str, str] = {}
                seen: Dict[str, bool] = {}   # 追踪哪些字段已出现过，避免重复事件
                horizon_final = None
                chunk_count = 0

                # DB 更新使用短生命周期 session，避免长期占用连接池
                # 优化：批量聚合DB更新，每2秒或累计≥5个字段才写一次，减少线程池压力
                _pending_db_updates: Dict[str, str] = {}
                _last_db_flush = time.time()

                def _horizon_partial_update(updates: dict):
                    with get_db_ctx() as _hdb:
                        report_service.update_report_partial(_hdb, job_id, **updates)

                def _flush_horizon_db():
                    nonlocal _pending_db_updates, _last_db_flush
                    if not _pending_db_updates:
                        return
                    try:
                        _horizon_partial_update(dict(_pending_db_updates))
                    except Exception as _flush_err:
                        logger.warning(f"[Job {job_id}] DB flush failed (non-fatal): {_flush_err}")
                    _pending_db_updates.clear()
                    _last_db_flush = time.time()

                # 通过 ContextVar 将 tracker 传入 async 节点（LangGraph 不传递 schema 外的字段）
                _tracker_token = current_tracker_var.set(h_tracker)
                _log(f"[Checkpoint] Job {job_id}: horizon '{horizon}' entering astream loop")
                try:
                    async for chunk in horizon_graph.graph.astream(init_state, **h_args):
                        chunk_count += 1
                        if chunk_count == 1:
                            _log(f"[Checkpoint] Job {job_id}: horizon '{horizon}' received first astream chunk (elapsed {time.time() - horizon_start_t:.2f}s)")
                        horizon_final = chunk

                        # ── 并行感知的状态推进 ──────────────────
                        # 1. 每个 analyst 报告首次出现 → completed
                        for analyst_key in ANALYST_ORDER:
                            if analyst_key not in horizon_analysts:
                                continue
                            rkey = ANALYST_REPORT_MAP[analyst_key]
                            aname = ANALYST_AGENT_NAMES[analyst_key]
                            if chunk.get(rkey) and not seen.get(rkey):
                                seen[rkey] = True
                                h_tracker._set_status(aname, "completed")

                        # 2. 分析师全部完成后 → Bull/Bear/ResearchManager 开始
                        all_analysts_done = all(
                            seen.get(ANALYST_REPORT_MAP.get(a, "")) for a in h_tracker.selected_analysts
                        )
                        if all_analysts_done and not seen.get("_research_started"):
                            seen["_research_started"] = True
                            h_tracker._set_status(ANALYST_AGENT_NAMES["bull"], "in_progress")
                            h_tracker._set_status(ANALYST_AGENT_NAMES["bear"], "in_progress")
                            h_tracker._set_status(ANALYST_AGENT_NAMES["research_manager"], "in_progress")

                        # 3. research judge → 研究团队完成, Trader 开始
                        debate = chunk.get("investment_debate_state") or {}
                        if debate.get("judge_decision") and not seen.get("judge_decision"):
                            seen["judge_decision"] = True
                            for r_key in ["bull", "bear", "research_manager"]:
                                h_tracker._set_status(ANALYST_AGENT_NAMES[r_key], "completed")
                            h_tracker._set_status(ANALYST_AGENT_NAMES["trader"], "in_progress")
                            h_tracker._emit_writing_status(ANALYST_AGENT_NAMES["trader"], "trader_investment_plan")

                        # 4. trader plan → Trader completed, 风控开始
                        if chunk.get("trader_investment_plan") and not seen.get("trader_investment_plan"):
                            seen["trader_investment_plan"] = True
                            h_tracker._set_status(ANALYST_AGENT_NAMES["trader"], "completed")
                            h_tracker._set_status(ANALYST_AGENT_NAMES["aggressive"], "in_progress")

                        # 5. risk judge → 风控全部完成
                        risk = chunk.get("risk_debate_state") or {}
                        if risk.get("judge_decision") and not seen.get("risk_judge_decision"):
                            seen["risk_judge_decision"] = True
                            for r_key in ["aggressive", "neutral", "conservative", "portfolio_manager"]:
                                h_tracker._set_status(ANALYST_AGENT_NAMES[r_key], "completed")
                        # ── end 并行感知 ────────────────────────────────────────────

                        # 报告分片推送与数据库批量更新（优化：聚合写DB，减少线程池竞争）
                        db_updates = {}
                        for key in report_keys:
                            value = chunk.get(key)
                            if value and value != last_report.get(key):
                                last_report[key] = value
                                db_updates[key] = str(value)
                                h_tracker._emit_report_chunked(job_id, key, str(value))

                        if db_updates:
                            _pending_db_updates.update(db_updates)
                            # 触发条件：累计≥5个字段 或 距上次flush≥2秒
                            if (len(_pending_db_updates) >= 5 or (time.time() - _last_db_flush) >= 2.0):
                                _flush_horizon_db()
                    # 循环结束：强制flush剩余更新
                    _flush_horizon_db()
                except Exception as e:
                    logger.error(
                        f"[Checkpoint] Job {job_id}: horizon '{horizon}' astream FAILED "
                        f"after {chunk_count} chunks ({time.time() - horizon_start_t:.2f}s): "
                        f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
                    )
                    raise
                finally:
                    current_tracker_var.reset(_tracker_token)

                _log(
                    f"[Checkpoint] Job {job_id}: horizon '{horizon}' astream completed "
                    f"({chunk_count} chunks, {time.time() - horizon_start_t:.2f}s)"
                )
                horizon_states[horizon] = horizon_final
                for agent, st in h_tracker.status.items():
                    if st not in ("completed", "skipped"):
                        h_tracker._set_status(agent, "completed")
                _emit_job_event(job_id, "agent.horizon_done", {"horizon": horizon})

            # 3. 按解析出的 horizons 并行运行 astream()，事件实时推给前端
            results = await asyncio.gather(
                *[_process_horizon(h) for h in request.horizons],
                return_exceptions=True,
            )
            horizon_errors = []
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.error(
                        f"[Checkpoint] Job {job_id}: horizon '{request.horizons[i]}' failed: "
                        f"{type(r).__name__}: {r}\n{''.join(traceback.format_exception(type(r), r, r.__traceback__))}"
                    )
                    horizon_errors.append(f"{request.horizons[i]}: {r}")
            if horizon_errors:
                raise RuntimeError(f"Horizon analysis failed: {'; '.join(horizon_errors)}")

            short_r = graph._build_horizon_result("short", horizon_states.get("short") or {})
            medium_r = graph._build_horizon_result("medium", horizon_states.get("medium") or {})
            primary_r = short_r if horizon_states.get("short") else medium_r
            decision = graph.process_signal(primary_r.get("final_trade_decision", "")) or "UNKNOWN"
            result = {
                "symbol": ticker,
                "trade_date": request.trade_date,
                "mode": "dual_horizon",
                "user_intent": user_intent,
                "short_term": short_r,
                "medium_term": medium_r,
                "decision": decision,
                # Hoist primary horizon's report fields to top level so that
                # resolve_report_fields / create_report can find them directly.
                "final_trade_decision": primary_r.get("final_trade_decision", ""),
                "investment_plan": primary_r.get("investment_plan", ""),
                "trader_investment_plan": primary_r.get("trader_investment_plan", ""),
                "market_report": primary_r.get("market_report", ""),
                "sentiment_report": primary_r.get("sentiment_report", ""),
                "news_report": primary_r.get("news_report", ""),
                "fundamentals_report": primary_r.get("fundamentals_report", ""),
                "macro_report": primary_r.get("macro_report", ""),
                "smart_money_report": primary_r.get("smart_money_report", ""),
                "volume_price_report": primary_r.get("volume_price_report", ""),
                "analyst_traces": (
                    short_r.get("analyst_traces", []) + medium_r.get("analyst_traces", [])
                ),
            }
            _log(f"[Checkpoint] Job {job_id}: all horizons completed, starting structured extraction")
            # LLM 结构化提取（目标价、止损、信心、风险、关键指标）
            # 注意：必须在 _set_job(status="completed") 之前完成，否则 SSE 超时
            # 会因为看到 status="completed" 而提前关闭流，导致 job.completed 事件丢失。
            structured = None
            try:
                structured = await asyncio.to_thread(
                    report_service.extract_structured_data,
                    final_trade_decision=primary_r.get("final_trade_decision", ""),
                    fundamentals_report=primary_r.get("fundamentals_report", ""),
                    config=config,
                )
                _log(f"[Checkpoint] Job {job_id}: structured extraction completed")
            except Exception as e:
                logger.error(f"[Checkpoint] Job {job_id}: structured extraction failed (non-fatal): {e}\n{traceback.format_exc()}")

            resolved = await asyncio.to_thread(
                report_service.resolve_report_fields,
                result_data=result,
                confidence_override=structured.confidence if structured else None,
                target_price_override=structured.target_price if structured else None,
                stop_loss_override=structured.stop_loss_price if structured else None,
            )
            result.update({
                "direction": resolved["direction"],
                "confidence": resolved["confidence"],
                "target_price": resolved["target_price"],
                "stop_loss_price": resolved["stop_loss_price"],
            })

            # 自动保存报告到数据库
            _log(f"[Checkpoint] Job {job_id}: saving report to DB (save_report={save_report})")
            if save_report:
                def _save_report_sync():
                    with get_db_ctx() as save_db:
                        report_service.create_report(
                            db=save_db,
                            symbol=request.symbol,
                            trade_date=request.trade_date,
                            decision=decision,
                            result_data=result,
                            user_id=user_id,
                            risk_items=([r.model_dump() for r in structured.risks] if structured else None),
                            key_metrics=([m.model_dump() for m in structured.key_metrics] if structured else None),
                            confidence_override=result["confidence"],
                            target_price_override=result["target_price"],
                            stop_loss_override=result["stop_loss_price"],
                            report_id=job_id,
                            analyst_traces=result.get("analyst_traces"),
                        )
                        save_db.commit()

                try:
                    await asyncio.to_thread(_save_report_sync)
                    _log(f"[Checkpoint] Job {job_id}: report saved to DB successfully")
                except Exception as e:
                    logger.error(f"[Checkpoint] Job {job_id}: failed to save report to DB: {e}\n{traceback.format_exc()}")

            # 所有后处理完成后再标记 completed，防止 SSE 超时提前关闭流
            _log(f"[Checkpoint] Job {job_id}: marking job as completed (dual_horizon)")
            _set_job(job_id, status="completed", result=result,
                     decision=decision, finished_at=_utcnow_iso())
            _emit_job_event(job_id, "job.completed", {
                "job_id": job_id, "decision": decision,
                "direction": result["direction"],
                "result": result, "mode": "dual_horizon",
                "risk_items": [r.model_dump() for r in structured.risks] if structured else [],
                "key_metrics": [m.model_dump() for m in structured.key_metrics] if structured else [],
                "confidence": result["confidence"],
                "target_price": result["target_price"],
                "stop_loss_price": result["stop_loss_price"],
            })
            _log(f"Job completed successfully: {job_id}")
            _log(f"[Timer] TOTAL Job execution (dual_horizon) took {time.time() - job_start_t:.2f}s")
            return
        # ── End dual-horizon path ─────────────────────────────────────────────

        if stream_events:
            init_state = graph.propagator.create_initial_state(
                request.symbol,
                request.trade_date,
                user_context=user_context_payload,
                selected_analysts=request.selected_analysts,
                request_source=request_source,
            )
            args = graph.propagator.get_graph_args()

            # Pass job_id as thread_id for LangGraph checkpointer persistence
            if "config" not in args:
                args["config"] = {}
            args["config"]["configurable"] = {"thread_id": job_id}

            report_keys = (
                "market_report",
                "sentiment_report",
                "news_report",
                "fundamentals_report",
                "macro_report",
                "smart_money_report",
                "volume_price_report",
                "investment_plan",
                "trader_investment_plan",
                "final_trade_decision",
            )
            last_report: Dict[str, str] = {}
            seen: Dict[str, bool] = {}

            # 优化：批量聚合DB更新，减少线程池竞争
            _pending_db_updates_single: Dict[str, str] = {}
            _last_db_flush_single = time.time()

            def _flush_single_db():
                nonlocal _pending_db_updates_single, _last_db_flush_single
                if not _pending_db_updates_single:
                    return
                try:
                    with get_db_ctx() as _db:
                        report_service.update_report_partial(_db, job_id, **_pending_db_updates_single)
                except Exception as _flush_err:
                    logger.warning(f"[Job {job_id}] single-horizon DB flush failed (non-fatal): {_flush_err}")
                _pending_db_updates_single.clear()
                _last_db_flush_single = time.time()

            _tracker_token = current_tracker_var.set(tracker)
            _log(f"[Checkpoint] Job {job_id}: entering single-horizon astream loop (stream_events=True)")
            _stream_chunk_count = 0
            try:
                async for chunk in graph.graph.astream(init_state, **args):
                    _stream_chunk_count += 1
                    if _stream_chunk_count == 1:
                        _log(f"[Checkpoint] Job {job_id}: received first astream chunk")
                    final_state = chunk
                    # ── 并行感知的状态推进 ──────────────────
                    # 1. 每个 analyst 报告首次出现 → completed
                    for analyst_key in ANALYST_ORDER:
                        if analyst_key not in request.selected_analysts:
                            continue
                        rkey = ANALYST_REPORT_MAP[analyst_key]
                        aname = ANALYST_AGENT_NAMES[analyst_key]
                        if chunk.get(rkey) and not seen.get(rkey):
                            seen[rkey] = True
                            tracker._set_status(aname, "completed")

                    # 2. 分析师全部完成 → 研究团队开始
                    all_analysts_done = all(
                        seen.get(ANALYST_REPORT_MAP.get(a, "")) for a in tracker.selected_analysts
                    )
                    if all_analysts_done and not seen.get("_research_started"):
                        seen["_research_started"] = True
                        tracker._set_status(ANALYST_AGENT_NAMES["bull"], "in_progress")
                        tracker._set_status(ANALYST_AGENT_NAMES["bear"], "in_progress")
                        tracker._set_status(ANALYST_AGENT_NAMES["research_manager"], "in_progress")

                    debate = chunk.get("investment_debate_state") or {}
                    if debate.get("judge_decision") and not seen.get("judge_decision"):
                        seen["judge_decision"] = True
                        for r_key in ["bull", "bear", "research_manager"]:
                            tracker._set_status(ANALYST_AGENT_NAMES[r_key], "completed")
                        tracker._set_status(ANALYST_AGENT_NAMES["trader"], "in_progress")

                    if chunk.get("trader_investment_plan") and not seen.get("trader_investment_plan"):
                        seen["trader_investment_plan"] = True
                        tracker._set_status(ANALYST_AGENT_NAMES["trader"], "completed")
                        tracker._set_status(ANALYST_AGENT_NAMES["aggressive"], "in_progress")

                    risk = chunk.get("risk_debate_state") or {}
                    if risk.get("judge_decision") and not seen.get("risk_judge_decision"):
                        seen["risk_judge_decision"] = True
                        for r_key in ["aggressive", "neutral", "conservative", "portfolio_manager"]:
                            tracker._set_status(ANALYST_AGENT_NAMES[r_key], "completed")
                    # ────────────────────────────────────────────

                    # ── Partial DB Persistence & UI Streaming ──
                    db_updates = {}
                    for key in report_keys:
                        value = chunk.get(key)
                        if value and value != last_report.get(key):
                            last_report[key] = value
                            db_updates[key] = str(value)
                            # 立即推送报告分片，前端即可"即产即看"
                            tracker._emit_report_chunked(job_id, key, str(value))

                    if db_updates:
                        _pending_db_updates_single.update(db_updates)
                        # 触发条件：累计≥5个字段 或 距上次flush≥2秒
                        if (len(_pending_db_updates_single) >= 5 or (time.time() - _last_db_flush_single) >= 2.0):
                            _flush_single_db()
                # 循环结束：强制flush剩余更新
                _flush_single_db()

                # ── Message & Tool Call Handling ──
                messages = chunk.get("messages", [])
                if messages:
                    msg = messages[-1]
                    content = _extract_message_text(getattr(msg, "content", ""))
                    agent_name = getattr(msg, "name", None)

                    if content:
                        _log(f"[Agent Message] {agent_name}: {content[:200]}...")

                    for tool_call in getattr(msg, "tool_calls", []) or []:
                        tool_name = tool_call.get("name", "unknown") if isinstance(tool_call, dict) else getattr(tool_call, "name", "unknown")
                        tool_args = tool_call.get("args", {}) if isinstance(tool_call, dict) else getattr(tool_call, "args", {})
                        _log(f"[Tool Call] {agent_name}: {tool_name}")

                        agent_display = agent_name
                        if not agent_display:
                            tool_to_agent = {
                                "get_stock_data": "数据获取",
                                "get_indicators": "技术分析师",
                                "get_fundamentals": "基本面分析师",
                                "get_income_statement": "基本面分析师",
                                "get_balance_sheet": "基本面分析师",
                                "get_cash_flow": "基本面分析师",
                                "get_news": "新闻分析师",
                                "get_social_sentiment": "舆情分析师",
                            }
                            agent_display = tool_to_agent.get(tool_name, "系统")

                        tool_description = _generate_tool_description(tool_name, tool_args)
                        _emit_job_event(
                            job_id,
                            "agent.tool_call",
                            {
                                "agent": agent_display,
                                "tool": tool_name,
                                "description": tool_description,
                            },
                        )

            except Exception as e:
                logger.error(
                    f"[Checkpoint] Job {job_id}: single-horizon astream FAILED "
                    f"after {_stream_chunk_count} chunks: {type(e).__name__}: {e}\n{traceback.format_exc()}"
                )
                raise
            finally:
                current_tracker_var.reset(_tracker_token)
        else:
            final_state, _ = await asyncio.to_thread(
                graph.propagate,
                request.symbol,
                request.trade_date,
                user_context=user_context_payload,
                selected_analysts=request.selected_analysts,
                request_source=request_source,
                thread_id=job_id,
            )

        if not final_state:
            raise RuntimeError("graph returned empty final state")

        decision = graph.process_signal(final_state["final_trade_decision"]) or "UNKNOWN"
        result = _build_result_payload(final_state)
        result["decision"] = decision

        # 全量收口为 completed/skipped
        for agent, status in tracker.status.items():
            if status not in ("completed", "skipped"):
                tracker._set_status(agent, "completed")

        # LLM 结构化提取（非阻塞，失败不影响主流程）
        # 注意：_set_job(status="completed") 必须在此之后调用，否则 SSE 超时会提前关闭流
        structured = None
        try:
            structured = await asyncio.to_thread(
                report_service.extract_structured_data,
                final_trade_decision=result.get("final_trade_decision", ""),
                fundamentals_report=result.get("fundamentals_report", ""),
                config=config,
            )
        except Exception as e:
            _log(f"Structured extraction failed (non-fatal): {e}")

        # 一次性解析所有字段（方向、信心、目标价等）
        resolved = await asyncio.to_thread(
            report_service.resolve_report_fields,
            result_data=result,
            confidence_override=structured.confidence if structured else None,
            target_price_override=structured.target_price if structured else None,
            stop_loss_override=structured.stop_loss_price if structured else None,
        )

        # 注入结果字典以便通知和保存使用
        result.update({
            "direction": resolved["direction"],
            "confidence": resolved["confidence"],
            "target_price": resolved["target_price"],
            "stop_loss_price": resolved["stop_loss_price"],
        })

        # 自动保存/收口报告到数据库
        if save_report:
            def _save_report_final_sync():
                with get_db_ctx() as save_db:
                    try:
                        report_service.create_report(
                            db=save_db,
                            symbol=request.symbol,
                            trade_date=request.trade_date,
                            decision=decision,
                            result_data=result,
                            user_id=user_id,
                            risk_items=([r.model_dump() for r in structured.risks] if structured else None),
                            key_metrics=([m.model_dump() for m in structured.key_metrics] if structured else None),
                            confidence_override=result["confidence"],
                            target_price_override=result["target_price"],
                            stop_loss_override=result["stop_loss_price"],
                            report_id=job_id,
                            analyst_traces=result.get("analyst_traces"),
                        )
                        save_db.commit()
                    except Exception as e:
                        try:
                            save_db.rollback()
                        except Exception:
                            pass
                        raise

            try:
                await asyncio.to_thread(_save_report_final_sync)
            except Exception as e:
                _log(f"Failed to finalize report (DB save error): {e}")
        # 所有后处理完成后再标记 completed，防止 SSE 超时提前关闭流
        _set_job(
            job_id,
            status="completed",
            result=result,
            decision=decision,
            finished_at=_utcnow_iso(),
        )
        _emit_job_event(
            job_id,
            "job.completed",
            {
                "job_id": job_id,
                "decision": decision,
                "direction": result["direction"],
                "result": result,
                "risk_items": [r.model_dump() for r in structured.risks] if structured else [],
                "key_metrics": [m.model_dump() for m in structured.key_metrics] if structured else [],
                "confidence": result["confidence"],
                "target_price": result["target_price"],
                "stop_loss_price": result["stop_loss_price"],
            },
        )
        _log(f"Job completed successfully: {job_id}")
        _log(f"[Timer] TOTAL Job execution (single_horizon) took {time.time() - job_start_t:.2f}s")
    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}"
        _set_job(
            job_id,
            status="failed",
            error=err_msg,
            traceback=traceback.format_exc(),
            finished_at=_utcnow_iso(),
        )

        # ── Persistent failure recording (short-lived session) ──
        try:
            def _record_failure():
                with get_db_ctx() as err_db:
                    report_service.mark_report_failed(err_db, job_id, f"{err_msg}\n\n{traceback.format_exc()}")
            await asyncio.to_thread(_record_failure)
        except Exception as db_exc:
            _log(f"Failed to record failure in DB: {db_exc}")

        _emit_job_event(
            job_id,
            "job.failed",
            {"job_id": job_id, "error": err_msg},
        )
    finally:
        _shared_data_collector.evict(request.symbol, request.trade_date)
