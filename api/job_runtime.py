"""Job runtime: store singleton, progress tracking, SSE streaming, ownership.

Extracted from ``api/main.py``.  All symbols are re-exported from
``api.main`` so existing ``from api.main import ...`` imports keep working.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from threading import Lock
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from api.database import UserDB
from api.job_store import get_job_store as _new_job_store

logger = logging.getLogger(__name__)


# ── Singleton job store (in-memory or Redis depending on REDIS_URL) ─────────
_job_store_instance: Optional[Any] = None


def get_job_store():
    global _job_store_instance
    if _job_store_instance is None:
        _job_store_instance = _new_job_store()
    return _job_store_instance


# Hold references to fire-and-forget tasks so they are not garbage collected
_background_tasks: set = set()

# Soft deadline for long-running analyses.  Keeps the historical
# TA_JOB_TIMEOUT setting API-compatible, but crossing it now emits an overtime
# notification instead of terminating the job.  TA_JOB_HARD_TIMEOUT is the
# resource-safety backstop that releases scheduler capacity if a workflow is
# genuinely wedged.  Set either value to 0 to disable that deadline.
_JOB_TIMEOUT = int(os.getenv("TA_JOB_TIMEOUT", "1800"))
_JOB_HARD_TIMEOUT = int(os.getenv("TA_JOB_HARD_TIMEOUT", "7200"))


def _create_tracked_task(coro, *, label: str = "Background task") -> asyncio.Task:
    """Create an asyncio task and keep a reference to prevent GC.
    Also logs unhandled exceptions via a done callback."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)

    def _on_done(t: asyncio.Task):
        _background_tasks.discard(t)
        if not t.cancelled() and t.exception():
            logger.error("%s failed: %s", label, t.exception())

    task.add_done_callback(_on_done)
    return task


def _utcnow_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _set_job(job_key: str, **kwargs) -> None:
    # Callers may pass job_id=<value> as a stored field.  Since
    # store.set_job()'s first positional param is also called job_id,
    # we must strip it from kwargs to avoid a "got multiple values" TypeError.
    # _get_job() always injects job_id back into the returned dict.
    kwargs.pop("job_id", None)
    get_job_store().set_job(job_key, **kwargs)


def _get_job(job_key: str) -> Dict[str, Any]:
    d = get_job_store().get_job(job_key)
    if d:
        d.setdefault("job_id", job_key)
    return d


def _emit_job_event(job_id: str, event: str, data: Dict[str, Any]) -> None:
    get_job_store().emit_event(job_id, event, data)


def _attach_job_runtime_state(target: Any, job_id: Optional[str]) -> Any:
    if not job_id:
        return target
    job = _get_job(job_id)
    if not job:
        return target

    for field in ("waiting_ahead_count", "scheduled_running_count", "scheduled_concurrency_limit"):
        value = job.get(field)
        if value is not None or hasattr(target, field):
            setattr(target, field, value)
    return target


# ── Analyst team constants ───────────────────────────────────────────────────

FIXED_TEAMS = {
    "Analyst Team": [
        "Market Analyst",
        "Social Analyst",
        "News Analyst",
        "Fundamentals Analyst",
        "Macro Analyst",
        "Smart Money Analyst",
        "Volume Price Analyst",
    ],
    "Research Team": ["Bull Researcher", "Bear Researcher", "Research Manager"],
    "Trading Team": ["Trader"],
    "Risk Management": ["Aggressive Analyst", "Neutral Analyst", "Conservative Analyst"],
    "Portfolio Management": ["Portfolio Manager"],
}
ANALYST_ORDER = ["market", "news", "fundamentals", "macro", "smart_money"]
ANALYST_AGENT_NAMES = {
    "market": "Market Analyst",
    "social": "Social Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
    "macro": "Macro Analyst",
    "volume_price": "Volume Price Analyst",
    "smart_money": "Smart Money Analyst",
    "bull": "Bull Researcher",
    "bear": "Bear Researcher",
    "Bull_Initial": "Bull Researcher",
    "Bear_Initial": "Bear Researcher",
    "Bull_Rebuttal": "Bull Researcher",
    "Bear_Rebuttal": "Bear Researcher",
    "research_manager": "Research Manager",
    "trader": "Trader",
    "aggressive": "Aggressive Analyst",
    "neutral": "Neutral Analyst",
    "conservative": "Conservative Analyst",
    "portfolio_manager": "Portfolio Manager",
}
ANALYST_REPORT_MAP = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
    "macro": "macro_report",
    "smart_money": "smart_money_report",
    "volume_price": "volume_price_report",
}


# ── Agent progress tracker ───────────────────────────────────────────────────

class AgentProgressTracker:
    # 阶段标题映射
    STAGE_TITLES = {
        "market_analysis": "市场分析完成",
        "sentiment_analysis": "舆情分析完成",
        "news_analysis": "新闻分析完成",
        "fundamentals_analysis": "基本面分析完成",
        "research_decision": "研究团队决策",
        "trader_plan": "交易计划制定",
        "risk_assessment": "风险评估完成",
        "final_decision": "最终决策",
    }

    def __init__(self, selected_analysts: List[str], job_id: str, horizon: Optional[str] = None):
        self.job_id = job_id
        self.horizon = horizon
        self.selected_analysts = [a.lower() for a in selected_analysts]
        self.status: Dict[str, str] = {}
        self.start_times: Dict[str, float] = {}  # 记录每个 agent 开始时间
        self.report_sections: Dict[str, Optional[str]] = {
            "market_report": None,
            "sentiment_report": None,
            "news_report": None,
            "fundamentals_report": None,
            "macro_report": None,
            "smart_money_report": None,
            "volume_price_report": None,
            "game_theory_report": None,
            "investment_plan": None,
            "trader_investment_plan": None,
            "final_trade_decision": None,
        }
        self._lock = Lock()
        # 跟踪已完成的阶段，避免重复发送里程碑
        self._completed_stages: set = set()
        # 跟踪已发送的 writing 状态，避免重复发送
        self._writing_status_sent: set = set()

        for team_agents in FIXED_TEAMS.values():
            for agent in team_agents:
                self.status[agent] = "pending"

        # 未选中的分析师标记为 skipped（仍展示，便于固定 12-agent 看板）
        for key in ANALYST_ORDER:
            agent = ANALYST_AGENT_NAMES[key]
            if key not in self.selected_analysts:
                self.status[agent] = "skipped"

    def _emit_milestone(self, stage: str, summary: str = "") -> None:
        """发送用户可见的里程碑事件"""
        with self._lock:
            if stage in self._completed_stages:
                return
            self._completed_stages.add(stage)

        title = self.STAGE_TITLES.get(stage, stage)
        _emit_job_event(
            self.job_id,
            "agent.milestone",
            {
                "stage": stage,
                "title": title,
                "summary": summary,
                "timestamp": _utcnow_iso(),
                "horizon": self.horizon,
            },
        )
        logger.info("[Milestone] %s: %s...", title, summary[:100])

    def _emit_report_chunked(self, job_id: str, section: str, content: str) -> None:
        """将报告内容分片发送，直接透传不做人工延迟

        按较大块分片（如按段落），让前端自然渲染
        优化：短内容（<800字符）直接整段发送，避免过度分片
        """
        # 短内容直接整段发送，减少事件数量
        if len(content) < 800:
            if content.strip():
                _emit_job_event(
                    job_id,
                    "agent.report.chunk",
                    {
                        "section": section,
                        "chunk": content,
                        "index": 0,
                        "is_complete": False,
                        "horizon": self.horizon,
                    },
                )
            _emit_job_event(
                job_id,
                "agent.report.chunk",
                {
                    "section": section,
                    "chunk": "",
                    "index": -1,
                    "is_complete": True,
                    "horizon": self.horizon,
                },
            )
            return

        # 按段落分割，保持Markdown结构
        paragraphs = content.split('\n\n')

        for i, para in enumerate(paragraphs):
            if not para.strip():
                continue

            _emit_job_event(
                job_id,
                "agent.report.chunk",
                {
                    "section": section,
                    "chunk": para + '\n\n',
                    "index": i,
                    "is_complete": False,
                    "horizon": self.horizon,
                },
            )

        # 发送完成标记
        _emit_job_event(
            job_id,
            "agent.report.chunk",
            {
                "section": section,
                "chunk": "",
                "index": -1,
                "is_complete": True,
                "horizon": self.horizon,
            },
        )

    def snapshot(self) -> Dict[str, Any]:
        agents = []
        for team, members in FIXED_TEAMS.items():
            for m in members:
                agents.append({"team": team, "agent": m, "status": self.status.get(m, "pending")})
        return {"agents": agents, "horizon": self.horizon}

    def _set_status(self, agent: str, status: str) -> None:
        with self._lock:
            prev = self.status.get(agent)
            if prev == status:
                return
            self.status[agent] = status

            # 记录时间
            if status == "in_progress":
                self.start_times[agent] = time.time()
            elif status == "completed" and agent in self.start_times:
                duration = time.time() - self.start_times[agent]
                logger.info("[Timer] Agent %s (%s) finished in %.2fs", agent, self.horizon or 'main', duration)

        _emit_job_event(
            self.job_id,
            "agent.status",
            {"agent": agent, "status": status, "previous_status": prev, "horizon": self.horizon},
        )

    def _update_research_team_status(self, status: str) -> None:
        for agent in ["Bull Researcher", "Bear Researcher", "Research Manager"]:
            self._set_status(agent, status)

    def _generate_stage_summary(self, stage: str, chunk: Dict[str, Any]) -> str:
        """根据阶段生成简要总结"""
        if stage == "market_analysis":
            report = chunk.get("market_report", "")
            # 提取关键信息
            if "支撑" in report or "压力" in report:
                return "技术面关键位已识别"
            return "技术面分析完成"
        elif stage == "sentiment_analysis":
            return "舆情数据已收集"
        elif stage == "news_analysis":
            return "新闻影响已评估"
        elif stage == "fundamentals_analysis":
            return "基本面指标已计算"
        elif stage == "research_decision":
            return "多空观点已形成"
        elif stage == "trader_plan":
            return "交易策略已制定"
        elif stage == "risk_assessment":
            return "风险水平已评估"
        elif stage == "final_decision":
            decision = chunk.get("final_trade_decision", "")
            return f"最终建议: {decision[:50]}..." if len(decision) > 50 else f"最终建议: {decision}"
        return ""

    def _emit_writing_status(self, agent_name: str, report_type: str) -> None:
        """发送正在编写报告的状态（每个agent只发送一次）"""
        # 检查是否已经发送过
        status_key = f"{agent_name}:{report_type}"
        with self._lock:
            if status_key in self._writing_status_sent:
                return
            self._writing_status_sent.add(status_key)

        report_names = {
            "market_report": "市场分析",
            "sentiment_report": "舆情分析",
            "news_report": "新闻分析",
            "fundamentals_report": "基本面分析",
            "investment_plan": "投资计划",
            "trader_investment_plan": "交易计划",
            "final_trade_decision": "最终交易决策",
        }
        _emit_job_event(
            self.job_id,
            "agent.writing",
            {
                "agent": agent_name,
                "report": report_type,
                "report_name": report_names.get(report_type, report_type),
                "status": "writing",
                "horizon": self.horizon,
            },
        )

    def _emit_token(self, agent_name: str, report_type: str, token: str) -> None:
        """推送 Token 级别的流式内容（跳过空 token，避免思维模型推理阶段刷屏）"""
        if not token:
            return
        _emit_job_event(
            self.job_id,
            "agent.token",
            {
                "agent": agent_name,
                "report": report_type,
                "token": token,
                "horizon": self.horizon,
            },
        )

    def emit_debate_token(
        self, debate: str, agent: str, round_num: int, token: str,
    ) -> None:
        """推送辩论 token（流式输出，每个 chunk 调用一次）"""
        if not token:
            return
        try:
            _emit_job_event(
                self.job_id,
                "agent.debate.token",
                {
                    "debate": debate,
                    "agent": agent,
                    "round": round_num,
                    "token": token,
                    "horizon": self.horizon,
                },
            )
        except Exception as e:
            logger.debug("tracker emit failed: %s", e)

    def emit_debate_message(
        self, debate: str, agent: str, round_num: int,
        content: str, is_verdict: bool = False,
    ) -> None:
        """推送辩论消息（每个 agent 每轮完成后调用一次）"""
        if not content:
            return
        try:
            _emit_job_event(
                self.job_id,
                "agent.debate",
                {
                    "debate": debate,
                    "agent": agent,
                    "round": round_num,
                    "content": content,
                    "is_verdict": is_verdict,
                    "horizon": self.horizon,
                },
            )
        except Exception:
            logging.getLogger(__name__).warning(
                "Failed to emit debate message for %s in %s", agent, debate, exc_info=True,
            )

    def apply_chunk(self, chunk: Dict[str, Any]) -> None:
        # 分析师阶段状态推进
        found_active = False
        for analyst_key in ANALYST_ORDER:
            if analyst_key not in self.selected_analysts:
                continue

            agent_name = ANALYST_AGENT_NAMES[analyst_key]
            report_key = ANALYST_REPORT_MAP[analyst_key]
            has_report = bool(chunk.get(report_key))

            if has_report:
                if self.status.get(agent_name) != "completed":
                    self._set_status(agent_name, "completed")
                    self.report_sections[report_key] = chunk.get(report_key)
            elif not found_active:
                # 只在状态从 pending 变为 in_progress 时发送 writing 状态
                prev_status = self.status.get(agent_name)
                if prev_status != "in_progress":
                    self._set_status(agent_name, "in_progress")
                    # 发送正在分析的状态（只发送一次）
                    self._emit_writing_status(agent_name, report_key)
                found_active = True
            else:
                self._set_status(agent_name, "pending")

        # 分析师全部完成后，启动 Bull Researcher
        if not found_active and self.selected_analysts:
            if self.status.get("Bull Researcher") == "pending":
                self._set_status("Bull Researcher", "in_progress")

        # 研究团队状态更新
        debate_state = chunk.get("investment_debate_state") or {}
        bull_hist = str(debate_state.get("bull_history", "")).strip()
        bear_hist = str(debate_state.get("bear_history", "")).strip()
        judge = str(debate_state.get("judge_decision", "")).strip()
        if bull_hist or bear_hist:
            self._update_research_team_status("in_progress")
        if judge:
            self._update_research_team_status("completed")
            if self.status.get("Trader") != "in_progress":
                self._set_status("Trader", "in_progress")
                self._emit_writing_status("Trader", "trader_investment_plan")

        # 交易团队
        if chunk.get("trader_investment_plan"):
            if self.status.get("Trader") != "completed":
                self._set_status("Trader", "completed")
                self._set_status("Aggressive Analyst", "in_progress")

        # 风控与组合团队（发送最终决策）
        risk_state = chunk.get("risk_debate_state") or {}
        risk_judge = str(risk_state.get("judge_decision", "")).strip()

        if risk_judge:
            if self.status.get("Portfolio Manager") != "completed":
                self._set_status("Portfolio Manager", "in_progress")
                self._set_status("Aggressive Analyst", "completed")
                self._set_status("Conservative Analyst", "completed")
                self._set_status("Neutral Analyst", "completed")
                self._set_status("Portfolio Manager", "completed")
                final_summary = self._generate_stage_summary("final_decision", chunk)
                self._emit_milestone("final_decision", final_summary)


# ── SSE streaming & ownership ────────────────────────────────────────────────

def _sse_pack(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream_job_events(job_id: str):
    import asyncio
    store = get_job_store()
    yield _sse_pack("job.ready", {"job_id": job_id})
    try:
        # JobStore.subscribe 已内置 ping 心跳（默认 8 秒一次），
        # 可保持连接活跃，防止代理/浏览器在长时间无业务事件时断开。
        async for event in store.subscribe(job_id):
            evt_name = event["event"]
            yield _sse_pack(evt_name, event["data"])
            if evt_name in ("job.completed", "job.failed"):
                yield "event: done\ndata: [DONE]\n\n"
                return
    except asyncio.CancelledError:
        # Client disconnected (e.g., browser closed or network timeout)
        # This is normal for long-running SSE streams, no need to crash
        logger.info("[SSE] Client disconnected for job %s", job_id)
        return
    except Exception as e:
        logger.info("[SSE] Error streaming job %s: %s", job_id, e)
        yield _sse_pack("job.failed", {"error": f"Stream error: {str(e)}"})
        yield "event: done\ndata: [DONE]\n\n"
        return


def _require_job_owner(job_id: str, current_user: UserDB) -> Dict[str, Any]:
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    owner_id = job.get("user_id")
    if owner_id and owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="job not found")
    return job
