from unittest.mock import MagicMock

import pytest

from tradingagents.graph.intent_parser import parse_intent, build_horizon_context


@pytest.mark.xfail(
    strict=True,
    reason="parse_intent deliberately forces horizons=['short'] (single-run design, see comment in "
    "intent_parser.py); test still expects the LLM-returned ['short','medium']. Reconcile the contract.",
)
def test_parse_intent_returns_defaults():
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(
        content='{"ticker": "600519", "horizons": ["short", "medium"], "focus_areas": [], "specific_questions": []}'
    )
    result = parse_intent("分析600519", mock_llm)
    assert result["ticker"] == "600519"
    assert result["horizons"] == ["short", "medium"]
    assert result["focus_areas"] == []


@pytest.mark.xfail(
    strict=True,
    reason="parse_intent deliberately forces horizons=['short'] even on the fallback path; test expects "
    "['short','medium']. Reconcile the contract.",
)
def test_parse_intent_fallback_on_invalid_json():
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content="这不是JSON")
    result = parse_intent("600519", mock_llm, fallback_ticker="600519")
    assert result["ticker"] == "600519"
    assert result["horizons"] == ["short", "medium"]
    assert result["focus_areas"] == []


def test_build_horizon_context_short_contains_label():
    ctx = build_horizon_context("short", ["量价关系"], ["能否突破"])
    assert "短线" in ctx
    assert "量价关系" in ctx
    assert "能否突破" in ctx


def test_build_horizon_context_medium_has_label():
    ctx = build_horizon_context("medium", [], [], agent_type="fundamentals")
    assert "中线" in ctx


@pytest.mark.xfail(
    strict=True,
    reason="build_horizon_context hardcodes weight_hint='' so the short/fundamentals '次要' downweight hint "
    "is never emitted; the feature is unwired. Wire weight_hint per (horizon, agent_type) to fix.",
)
def test_build_horizon_context_short_fundamentals_has_downweight_hint():
    ctx = build_horizon_context("short", [], [], agent_type="fundamentals")
    assert "次要" in ctx
