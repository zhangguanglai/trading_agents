"""Runtime config building, masking, probe and warmup helpers.

Extracted from ``api/main.py``.  All symbols are re-exported from
``api.main`` so existing ``from api.main import ...`` imports keep working.
"""

from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api.database import UserDB, get_db_ctx
from api.schemas import UserRuntimeConfigResponse, UserRuntimeConfigUpdateRequest
from api.services import auth_service

logger = logging.getLogger(__name__)


# ── Runtime config state / constants ──────────────────────────────────────────

# Runtime config overrides via PATCH /v1/config
_global_config_overrides: Dict[str, Any] = {}

# Allowlist for config_overrides from client requests.
# Security: prevents injection of api_key, backend_url, or other sensitive keys.
_CONFIG_OVERRIDES_ALLOWLIST = {
    "llm_provider", "deep_think_llm", "quick_think_llm",
    "max_debate_rounds", "max_risk_discuss_rounds",
    "prompt_language",
}

_CONFIG_ALLOWED_KEYS = {
    "llm_provider", "deep_think_llm", "quick_think_llm",
    "backend_url", "max_debate_rounds", "max_risk_discuss_rounds",
}
_CONFIG_PREFERENCE_KEYS = {"email_report_enabled", "wecom_report_enabled"}
_CONFIG_MODEL_KEYS = ("llm_provider", "backend_url", "quick_think_llm", "deep_think_llm")
_CONFIG_MODEL_LABELS = {
    "quick_think_llm": "常规模型",
    "deep_think_llm": "推理模型",
}
_CONFIG_PROBE_TIMEOUT_SECONDS = 12.0
_CONFIG_PROBE_PROMPT = "Reply with the single word OK."
_CONFIG_WARMUP_TIMEOUT_SECONDS = 20.0
_CONFIG_WARMUP_PROMPT = "Reply with the single word OK."


# ── Config building ───────────────────────────────────────────────────────────

def _deep_merge(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _user_config_overrides(user_id: Optional[str], db: Optional[Session] = None) -> Dict[str, Any]:
    if not user_id:
        return {}

    def _query(sess: Session) -> Dict[str, Any]:
        user_cfg = auth_service.get_user_llm_config(sess, user_id)
        if not user_cfg:
            return {}
        result: Dict[str, Any] = {}
        for key in (
            "llm_provider",
            "backend_url",
            "quick_think_llm",
            "deep_think_llm",
            "max_debate_rounds",
            "max_risk_discuss_rounds",
        ):
            value = getattr(user_cfg, key, None)
            if value is not None:
                result[key] = value
        api_key = auth_service.decrypt_secret(user_cfg.api_key_encrypted)
        if api_key:
            result["api_key"] = api_key
        return result

    if db is not None:
        return _query(db)
    with get_db_ctx() as own_db:
        return _query(own_db)


def _build_runtime_config(overrides: Dict[str, Any], user_id: Optional[str] = None, db: Optional[Session] = None) -> Dict[str, Any]:
    # Lazy import so that tests patching ``api.main.DEFAULT_CONFIG`` take effect.
    from api.main import DEFAULT_CONFIG
    config = deepcopy(DEFAULT_CONFIG)
    server_fallback_enabled = os.getenv("ALLOW_SERVER_LLM_FALLBACK", "1").strip().lower() in ("1", "true", "yes", "on")
    config["server_fallback_enabled"] = server_fallback_enabled

    # Security: filter request overrides to allowlist only
    overrides = {k: v for k, v in overrides.items() if k in _CONFIG_OVERRIDES_ALLOWLIST}

    # Apply global config overrides (from PATCH /v1/config)
    if _global_config_overrides:
        config = _deep_merge(config, dict(_global_config_overrides))

    # Fetch user specific overrides from DB (pass db to reuse caller's session)
    user_overrides = _user_config_overrides(user_id, db=db)

    # ── Critical: Filter out empty strings before merging ──
    # This prevents an empty DB field from wiping out an Env Var default.
    filtered_user_overrides = {k: v for k, v in user_overrides.items() if v not in (None, "", [])}
    filtered_request_overrides = {k: v for k, v in overrides.items() if v not in (None, "", [])}

    if filtered_user_overrides:
        config = _deep_merge(config, filtered_user_overrides)
    if filtered_request_overrides:
        config = _deep_merge(config, filtered_request_overrides)

    # ── Intelligent fallback between models ──
    # If one is provided but the other is missing (even after env var merge), cross-fill.
    quick = config.get("quick_think_llm")
    deep = config.get("deep_think_llm")

    if not deep and quick:
        config["deep_think_llm"] = quick
    if not quick and deep:
        config["quick_think_llm"] = deep

    return config


# ── Masking helpers ───────────────────────────────────────────────────────────

def _mask_secret_value(value: Optional[str], *, head: int = 4, tail: int = 4) -> Optional[str]:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    if len(normalized) <= head + tail:
        return "*" * max(6, len(normalized))
    return f"{normalized[:head]}{'*' * max(6, len(normalized) - head - tail)}{normalized[-tail:]}"


def _mask_wecom_webhook(webhook_url: Optional[str]) -> Optional[str]:
    normalized = str(webhook_url or "").strip()
    if not normalized:
        return None
    prefix = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key="
    if normalized.startswith(prefix):
        masked_key = _mask_secret_value(normalized[len(prefix):])
        return f"{prefix}{masked_key}"
    if normalized.startswith("http"):
        if "key=" in normalized:
            base, key = normalized.rsplit("key=", 1)
            return f"{base}key={_mask_secret_value(key)}"
        return _mask_secret_value(normalized, head=18, tail=8)
    return _mask_secret_value(normalized)


# ── Warmup / probe helpers ────────────────────────────────────────────────────

def _warmup_model_names(config: Dict[str, Any]) -> List[str]:
    seen: set[str] = set()
    models: List[str] = []
    for key in ("quick_think_llm", "deep_think_llm"):
        value = str(config.get(key) or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        models.append(value)
    return models


def _warmup_model_targets(config: Dict[str, Any]) -> List[Tuple[str, List[str]]]:
    targets: Dict[str, List[str]] = {}
    for key in ("quick_think_llm", "deep_think_llm"):
        model = str(config.get(key) or "").strip()
        if not model:
            continue
        labels = targets.setdefault(model, [])
        label = _CONFIG_MODEL_LABELS.get(key, key)
        if label not in labels:
            labels.append(label)
    return [(model, labels) for model, labels in targets.items()]


def _should_trigger_config_warmup(
    before_cfg: UserRuntimeConfigResponse,
    after_cfg: UserRuntimeConfigResponse,
    updates: UserRuntimeConfigUpdateRequest,
) -> bool:
    if not updates.warmup:
        return False
    if updates.force_warmup:
        return True
    if updates.api_key:
        return True
    before = before_cfg.model_dump()
    after = after_cfg.model_dump()
    return any(before.get(key) != after.get(key) for key in _CONFIG_MODEL_KEYS)


def _build_pending_runtime_config(
    updates: UserRuntimeConfigUpdateRequest,
    user_id: str,
    db: Session,
) -> Dict[str, Any]:
    config = _build_runtime_config({}, user_id=user_id, db=db)
    for key in _CONFIG_ALLOWED_KEYS:
        value = getattr(updates, key, None)
        if value is not None:
            config[key] = value

    if updates.clear_api_key:
        config["api_key"] = ""
    elif updates.api_key:
        config["api_key"] = updates.api_key

    quick = config.get("quick_think_llm")
    deep = config.get("deep_think_llm")
    if not deep and quick:
        config["deep_think_llm"] = quick
    if not quick and deep:
        config["quick_think_llm"] = deep
    return config


def _should_probe_runtime_config(
    before_cfg: UserRuntimeConfigResponse,
    pending_cfg: Dict[str, Any],
    updates: UserRuntimeConfigUpdateRequest,
) -> bool:
    del before_cfg, pending_cfg
    if updates.clear_api_key:
        return False
    return bool(updates.api_key)


def _probe_runtime_config(config: Dict[str, Any]) -> Dict[str, str]:
    from tradingagents.llm_clients.factory import create_llm_client

    provider = str(config.get("llm_provider") or "openai")
    base_url = config.get("backend_url")
    api_key = str(config.get("api_key") or "").strip()
    model = str(config.get("quick_think_llm") or config.get("deep_think_llm") or "").strip()

    if not model or not api_key:
        return {"status": "skipped", "reason": "missing_model_or_key"}

    try:
        client = create_llm_client(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            timeout=_CONFIG_PROBE_TIMEOUT_SECONDS,
            max_retries=0,
        )
        llm = client.get_llm()
        response = llm.invoke(_CONFIG_PROBE_PROMPT)
        raw = response if isinstance(response, str) else getattr(response, "content", str(response))
        preview = str(raw).strip().replace("\n", " ")[:80] or "<empty>"
        return {"status": "ok", "model": model, "preview": preview}
    except Exception as exc:
        detail = str(exc).strip()
        lowered = detail.lower()
        if "401" in lowered or "invalid authentication" in lowered or "authenticationerror" in lowered:
            raise HTTPException(
                status_code=400,
                detail="模型 Key 验证失败：上游返回 401 Invalid Authentication，请检查 API Key 是否正确。",
            ) from exc
        raise HTTPException(
            status_code=400,
            detail=f"模型连接验证失败：{detail[:200] or 'unknown error'}",
        ) from exc


def _invoke_runtime_warmup(
    config: Dict[str, Any],
    prompt: str,
    user_id: str,
    timeout: float = _CONFIG_WARMUP_TIMEOUT_SECONDS,
) -> List[Dict[str, Any]]:
    from tradingagents.llm_clients.factory import create_llm_client

    provider = str(config.get("llm_provider") or "openai")
    base_url = config.get("backend_url")
    api_key = config.get("api_key")
    targets = _warmup_model_targets(config)

    if not targets:
        raise HTTPException(status_code=400, detail="请先配置至少一个可用模型。")

    logger.info(
        "[LLM Warmup] user=%s invoking provider=%s models=%s base_url=%s",
        user_id,
        provider,
        [model for model, _ in targets],
        base_url or 'default',
    )

    results: List[Dict[str, Any]] = []
    errors: List[str] = []
    for model, labels in targets:
        try:
            client = create_llm_client(
                provider=provider,
                model=model,
                base_url=base_url,
                api_key=api_key,
                timeout=timeout,
                max_retries=0,
            )
            llm = client.get_llm()
            response = llm.invoke(prompt)
            raw = response if isinstance(response, str) else getattr(response, "content", str(response))
            content = str(raw).strip() or "<empty>"
            preview = content.replace("\n", " ")[:80]
            logger.info("[LLM Warmup] user=%s model=%s success response=%s", user_id, model, preview)
            results.append({
                "model": model,
                "targets": labels,
                "content": content,
                "error": None,
            })
        except Exception as exc:
            detail = str(exc).strip() or "unknown error"
            errors.append(f"{model}: {detail}")
            logger.warning(
                "[LLM Warmup] user=%s model=%s failed: %s",
                user_id,
                model,
                exc,
            )
            results.append({
                "model": model,
                "targets": labels,
                "content": None,
                "error": detail[:200],
            })

    if not any(item.get("content") for item in results):
        raise HTTPException(
            status_code=400,
            detail=f"模型 warmup 失败：{'; '.join(errors)[:300]}",
        )

    return results


def _run_config_warmup(config: Dict[str, Any], user_id: str) -> None:
    models = _warmup_model_names(config)
    if not models:
        logger.info("[LLM Warmup] user=%s skipped: no models configured", user_id)
        return
    try:
        _invoke_runtime_warmup(config, _CONFIG_WARMUP_PROMPT, user_id, timeout=_CONFIG_WARMUP_TIMEOUT_SECONDS)
    except HTTPException as exc:
        logger.warning("[LLM Warmup] user=%s failed: %s", user_id, exc.detail)


def _config_response_for_user(user: Optional[UserDB], db: Session) -> UserRuntimeConfigResponse:
    cfg = _build_runtime_config({}, user_id=user.id if user else None, db=db)
    user_cfg = auth_service.get_user_llm_config(db, user.id) if user else None
    webhook_url = auth_service.decrypt_secret(getattr(user_cfg, "wecom_webhook_encrypted", None))
    return UserRuntimeConfigResponse(
        llm_provider=cfg["llm_provider"],
        deep_think_llm=cfg["deep_think_llm"],
        quick_think_llm=cfg["quick_think_llm"],
        backend_url=cfg["backend_url"],
        max_debate_rounds=cfg["max_debate_rounds"],
        max_risk_discuss_rounds=cfg["max_risk_discuss_rounds"],
        has_api_key=bool(user_cfg and user_cfg.api_key_encrypted),
        has_wecom_webhook=bool(webhook_url),
        wecom_webhook_display=_mask_wecom_webhook(webhook_url),
        server_fallback_enabled=bool(cfg.get("server_fallback_enabled", True)),
        email_report_enabled=user.email_report_enabled if user and hasattr(user, 'email_report_enabled') else True,
        wecom_report_enabled=user.wecom_report_enabled if user and hasattr(user, "wecom_report_enabled") else True,
        default_analysts=json.loads(user_cfg.default_analysts) if user_cfg and user_cfg.default_analysts else ["market", "news", "fundamentals", "macro", "smart_money"],
    )
