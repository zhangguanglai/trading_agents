import asyncio

from langchain_core.messages import HumanMessage, SystemMessage
from tradingagents.dataflows.config import get_config
from tradingagents.prompts import get_prompt
from tradingagents.graph.intent_parser import build_horizon_context
from tradingagents.agents.utils.agent_states import current_tracker_var, extract_verdict


def create_smart_money_analyst(llm, data_collector=None):
    async def _safe(tool, payload):
        try:
            return await asyncio.to_thread(tool.invoke, payload)
        except Exception as exc:
            print(f"[SmartMoney._safe] {getattr(tool, 'name', str(tool))} failed: {type(exc).__name__}: {exc}")
            return None

    async def smart_money_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]
        print(f"[Smart Money Analyst] START {ticker} {current_date}")
        horizon = "short"  # 资金面固定短期视角
        user_intent = state.get("user_intent") or {}
        focus_areas = user_intent.get("focus_areas", [])
        specific_questions = user_intent.get("specific_questions", [])

        config = get_config()
        system_message = get_prompt("smart_money_system_message", config=config) or ""
        horizon_ctx = build_horizon_context(horizon, focus_areas, specific_questions, agent_type="smart_money")

        pool = data_collector.get(ticker, current_date) if data_collector else None

        # Detect HK stock and adjust data fetching strategy
        is_hk = ticker.strip().upper().endswith(".HK")

        if pool is not None:
            fund_flow = pool.get("fund_flow_individual", "无数据")
            lhb = pool.get("lhb", "无数据")
            volume = pool.get("indicators", {}).get("vwma", "无数据")
        else:
            from tradingagents.agents.utils.agent_utils import (
                get_individual_fund_flow, get_lhb_detail, get_indicators,
            )
            
            if is_hk:
                fund_flow = "港股暂无主力资金流向数据（Tushare未提供港股资金流向接口）。分析将基于成交量和价量关系进行。"
                lhb = "港股无龙虎榜机制。"
                volume = await _safe(get_indicators, {
                    "symbol": ticker, "indicator": "volume",
                    "curr_date": current_date, "look_back_days": 20,
                })
            else:
                results = await asyncio.gather(
                    _safe(get_individual_fund_flow, {"symbol": ticker}),
                    _safe(get_lhb_detail, {"symbol": ticker, "date": current_date}),
                    _safe(get_indicators, {
                        "symbol": ticker, "indicator": "volume",
                        "curr_date": current_date, "look_back_days": 20,
                    })
                )
                fund_flow, lhb, volume = results

        # 处理 None 值（API 失败时 _safe 返回 None）
        fund_flow = fund_flow or f"{ticker} 主力资金流向数据获取失败（网络/权限问题），请基于成交量和价量关系进行分析。"
        lhb = lhb or f"{ticker} 龙虎榜数据不可用（非异动日或接口异常），属于正常情况。"
        volume = volume or f"{ticker} 成交量指标（VWMA）暂无法获取。"

        messages = [
            SystemMessage(content=(
                system_message
                + "\n\n请严格基于提供的量化数据输出分析，全程使用中文。"
                + ("\n注意：港股分析缺少主力资金流向和龙虎榜数据，请重点基于成交量、价量关系、换手率等指标进行分析。" if is_hk else "")
            )),
            HumanMessage(content=(
                horizon_ctx + "\n"
                f"请分析 {ticker} 在 {current_date} 的主力资金行为。\n\n"
                f"【近5日主力资金净流向】\n{fund_flow}\n\n"
                f"【龙虎榜数据】\n{lhb}\n\n"
                f"【成交量指标(vwma)】\n{volume}"
            )),
        ]

        # ── 实现 Token 级流式输出 ──────────────────
        tracker = current_tracker_var.get()
        full_content = ""
        async for chunk in llm.astream(messages):
            content = chunk.content if hasattr(chunk, "content") else str(chunk)
            full_content += content
            if tracker:
                tracker._emit_token("Smart Money Analyst", "smart_money_report", content)

        print(f"[Smart Money Analyst] DONE {ticker}, report length={len(full_content)}")
        verdict, confidence = extract_verdict(full_content)
        return {
            "smart_money_report": full_content,
            "analyst_traces": [{
                "agent": "smart_money_analyst",
                "horizon": horizon,
                "data_window": "近期可用",
                "key_finding": f"主力资金分析结论：{verdict}",
                "verdict": verdict,
                "confidence": confidence,
            }],
        }

    return smart_money_analyst_node
