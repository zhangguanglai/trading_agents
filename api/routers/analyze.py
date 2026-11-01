"""Analysis & chat completion router."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

import api.main as _main
from api.database import UserDB, get_db_ctx
from api.dependencies import _require_api_user
from api.main import (
    AnalyzeRequest,
    AnalyzeResponse,
    ChatCompletionRequest,
    _apply_user_context_to_request,
    _build_runtime_config,
    _compose_analysis_user_context,
    _create_tracked_task,
    _emit_job_event,
    _extract_chat_text,
    _extract_request_user_context,
    _extract_symbol_and_date,
    _get_job,
    _normalize_symbol,
    _run_job,
    _search_cn_stock_by_name,
    _set_job,
    _stream_job_events,
    _utcnow_iso,
)
from tradingagents.dataflows.trade_calendar import cn_today_str

router = APIRouter(tags=["analyze"])


@router.post("/v1/analyze", response_model=AnalyzeResponse)
async def analyze(
    request: AnalyzeRequest,
    current_user: UserDB = Depends(_require_api_user),
) -> AnalyzeResponse:
    with get_db_ctx() as db:
        merged_user_context = _compose_analysis_user_context(
            db,
            current_user.id,
            request.symbol,
            explicit_context=_extract_request_user_context(request),
        )
    _apply_user_context_to_request(request, merged_user_context)

    job_id = uuid4().hex
    now = _utcnow_iso()
    _set_job(
        job_id,
        job_id=job_id,
        user_id=current_user.id,
        status="pending",
        created_at=now,
        started_at=None,
        finished_at=None,
        symbol=request.symbol,
        trade_date=request.trade_date,
        error=None,
        result=None,
        decision=None,
    )
    _emit_job_event(
        job_id,
        "job.created",
        {"job_id": job_id, "symbol": request.symbol, "trade_date": request.trade_date},
    )
    if request.dry_run:
        await _run_job(job_id, request, True, True, current_user.id, "api")
        final_status = _get_job(job_id).get("status", "completed")
        return AnalyzeResponse(job_id=job_id, status=final_status, created_at=now)
    _create_tracked_task(_run_job(job_id, request, True, True, current_user.id, "api"))
    return AnalyzeResponse(job_id=job_id, status="pending", created_at=now)


@router.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    current_user: UserDB = Depends(_require_api_user),
):
    text = _extract_chat_text(request.messages)
    config = await asyncio.to_thread(_build_runtime_config, request.config_overrides, user_id=current_user.id)

    # ── 流式模式：立刻返回 SSE 流，在后台异步提取意图再启动任务 ──────────────────
    # 这样用户提交查询后立刻收到 job.ready，不用等待 thinking 模型的 StockExtract。
    if request.stream:
        job_id = uuid4().hex

        async def _extract_and_run():
            try:
                # ── 快速路径：先用正则直接提取股票代码（无需 LLM）────────
                # 覆盖 "600522"、"600519.SH"、"0700.HK"、"AAPL" 等格式
                # 以及中文名称匹配（通过 _search_cn_stock_by_name）
                fast_symbol, fast_date = _extract_symbol_and_date(text)
                if not fast_symbol:
                    # 正则未命中，尝试从文本中提取中文公司名做本地查找
                    import re as _re
                    cn_name_match = _re.search(r'分析\s*([\u4e00-\u9fa5]+)|查看\s*([\u4e00-\u9fa5]+)|搜[\s]*([\u4e00-\u9fa5]+)', text)
                    if cn_name_match:
                        cn_candidate = next((g for g in cn_name_match.groups() if g), None)
                        if cn_candidate:
                            fast_symbol = await asyncio.to_thread(_search_cn_stock_by_name, cn_candidate)

                if fast_symbol:
                    symbol, trade_date = fast_symbol, fast_date or cn_today_str()
                    horizons, focus_areas, specific_questions, inferred_user_context = ["short"], [], [], {}
                    _emit_job_event(job_id, "job.created", {"symbol": symbol})
                else:
                    # ── 慢速路径：正则未命中，调用 LLM 提取 ────────────────
                    symbol, trade_date, horizons, focus_areas, specific_questions, inferred_user_context = \
                        await _main._ai_extract_symbol_and_date_streaming(text, config, job_id)

                if not symbol:
                    _emit_job_event(job_id, "job.failed", {
                        "error": "抱歉，我没能从您的消息中识别出股票标的。请输入代码（如 600519.SH）或可识别的公司名称。"
                    })
                    return

                pre_intent = {
                    "raw_query": text,
                    "ticker": symbol,
                    "horizons": horizons,
                    "focus_areas": focus_areas,
                    "specific_questions": specific_questions,
                }
                with get_db_ctx() as db:
                    merged_user_context = _compose_analysis_user_context(
                        db,
                        current_user.id,
                        symbol,
                        explicit_context=_extract_request_user_context(request),
                        inferred_context=inferred_user_context,
                    )
                pre_intent["user_context"] = merged_user_context
                analyze_req = AnalyzeRequest(
                    symbol=symbol,
                    trade_date=trade_date or cn_today_str(),
                    selected_analysts=request.selected_analysts,
                    config_overrides=request.config_overrides,
                    dry_run=request.dry_run,
                    query=text,
                    horizons=horizons,
                    user_intent=pre_intent,
                    objective=merged_user_context.get("objective"),
                    risk_profile=merged_user_context.get("risk_profile"),
                    investment_horizon=merged_user_context.get("investment_horizon"),
                    cash_available=merged_user_context.get("cash_available"),
                    current_position=merged_user_context.get("current_position"),
                    current_position_pct=merged_user_context.get("current_position_pct"),
                    average_cost=merged_user_context.get("average_cost"),
                    max_loss_pct=merged_user_context.get("max_loss_pct"),
                    constraints=merged_user_context.get("constraints", []),
                    user_notes=merged_user_context.get("user_notes"),
                )
                now = _utcnow_iso()
                _set_job(
                    job_id,
                    job_id=job_id,
                    user_id=current_user.id,
                    status="pending",
                    created_at=now,
                    started_at=None,
                    finished_at=None,
                    symbol=analyze_req.symbol,
                    trade_date=analyze_req.trade_date,
                    error=None,
                    result=None,
                    decision=None,
                )
                _emit_job_event(
                    job_id,
                    "job.created",
                    {"job_id": job_id, "symbol": analyze_req.symbol, "trade_date": analyze_req.trade_date},
                )
                await _run_job(job_id, analyze_req, True, True, current_user.id, "chat")
            except Exception as exc:
                import traceback
                _log(f"[chat] _extract_and_run failed: {exc}\n{traceback.format_exc()}")
                _emit_job_event(job_id, "job.failed", {"error": str(exc)})

        _create_tracked_task(_extract_and_run())
        return StreamingResponse(
            _stream_job_events(job_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    # ── 非流式模式：先走快速路径（正则），再走慢速路径（LLM）────────────────────
    fast_symbol, fast_date = _extract_symbol_and_date(text)
    if not fast_symbol:
        import re as _re
        cn_name_match = _re.search(r'分析\s*([\u4e00-\u9fa5]+)|查看\s*([\u4e00-\u9fa5]+)|搜[\s]*([\u4e00-\u9fa5]+)', text)
        if cn_name_match:
            cn_candidate = next((g for g in cn_name_match.groups() if g), None)
            if cn_candidate:
                fast_symbol = _search_cn_stock_by_name(cn_candidate)

    if fast_symbol:
        symbol, trade_date = fast_symbol, fast_date or cn_today_str()
        horizons, focus_areas, specific_questions, inferred_user_context = ["short"], [], [], {}
    else:
        symbol, trade_date, horizons, focus_areas, specific_questions, inferred_user_context = \
            await asyncio.to_thread(_main._ai_extract_symbol_and_date, text, config)

    if not symbol:
        raise HTTPException(status_code=400, detail="抱歉，我没能从您的消息中识别出股票标的。请输入代码（如 600519.SH）或可识别的公司名称。")

    pre_intent = {
        "raw_query": text,
        "ticker": symbol,
        "horizons": horizons,
        "focus_areas": focus_areas,
        "specific_questions": specific_questions,
    }
    with get_db_ctx() as db:
        merged_user_context = _compose_analysis_user_context(
            db,
            current_user.id,
            symbol,
            explicit_context=_extract_request_user_context(request),
            inferred_context=inferred_user_context,
        )
    pre_intent["user_context"] = merged_user_context
    analyze_req = AnalyzeRequest(
        symbol=symbol,
        trade_date=trade_date or cn_today_str(),
        selected_analysts=request.selected_analysts,
        config_overrides=request.config_overrides,
        dry_run=request.dry_run,
        query=text,
        horizons=horizons,
        user_intent=pre_intent,
        objective=merged_user_context.get("objective"),
        risk_profile=merged_user_context.get("risk_profile"),
        investment_horizon=merged_user_context.get("investment_horizon"),
        cash_available=merged_user_context.get("cash_available"),
        current_position=merged_user_context.get("current_position"),
        current_position_pct=merged_user_context.get("current_position_pct"),
        average_cost=merged_user_context.get("average_cost"),
        max_loss_pct=merged_user_context.get("max_loss_pct"),
        constraints=merged_user_context.get("constraints", []),
        user_notes=merged_user_context.get("user_notes"),
    )
    job_id = uuid4().hex
    now = _utcnow_iso()
    _set_job(
        job_id,
        job_id=job_id,
        user_id=current_user.id,
        status="pending",
        created_at=now,
        started_at=None,
        finished_at=None,
        symbol=analyze_req.symbol,
        trade_date=analyze_req.trade_date,
        error=None,
        result=None,
        decision=None,
    )
    _emit_job_event(
        job_id,
        "job.created",
        {"job_id": job_id, "symbol": analyze_req.symbol, "trade_date": analyze_req.trade_date},
    )
    if request.dry_run:
        await _run_job(job_id, analyze_req, True, True, current_user.id, "chat")
        status_text = _get_job(job_id).get("status", "completed")
        decision_text = _get_job(job_id).get("decision", "DRY_RUN")
        return {
            "id": f"chatcmpl-{job_id}",
            "object": "chat.completion",
            "created": int(datetime.now().timestamp()),
            "model": request.model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": (
                            f"已完成分析任务：{job_id}\n"
                            f"symbol={analyze_req.symbol}, trade_date={analyze_req.trade_date}\n"
                            f"status={status_text}, decision={decision_text}"
                        ),
                    },
                }
            ],
        }
    _create_tracked_task(_run_job(job_id, analyze_req, True, True, current_user.id, "chat"))
    return {
        "id": f"chatcmpl-{job_id}",
        "object": "chat.completion",
        "created": int(datetime.now().timestamp()),
        "model": request.model,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": (
                        f"已启动分析任务：{job_id}\n"
                        f"symbol={analyze_req.symbol}, trade_date={analyze_req.trade_date}\n"
                        f"可通过 /v1/jobs/{job_id} 与 /v1/jobs/{job_id}/result 查询结果。"
                    ),
                },
            }
        ],
    }
