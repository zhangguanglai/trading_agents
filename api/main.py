from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

import logging

# Configure standard logging to include timestamps
logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Re-export all Pydantic models from api.schemas so existing
# ``from api.main import <Model>`` imports keep working.
from api.schemas import *  # noqa: F401,F403
from api.schemas import _serialize_datetime_utc  # noqa: F401

# Re-export stock-map / symbol-normalization helpers from api.stock_map.
from api.stock_map import (  # noqa: F401
    _cn_stock_map,
    _cn_stock_reverse_map,
    _cn_stock_map_lock,
    _cn_stock_map_loaded_at,
    _STOCK_MAP_TTL,
    CN_INDEX_SYMBOL_MAP,
    _load_cn_stock_map,
    _get_reverse_stock_map,
    _get_reverse_stock_map_cached_only,
    _search_cn_stock_by_name,
    _split_watchlist_batch_text,
    _resolve_watchlist_identifier,
    _attach_stock_names,
    _normalize_symbol,
    _normalize_ths_code,
    _parse_stock_csv,
    _is_cn_index_symbol,
    _normalize_kline_df,
    _fetch_index_kline,
)

# Re-export runtime config helpers from api.runtime_config.
from api.runtime_config import (  # noqa: F401
    _global_config_overrides,
    _CONFIG_OVERRIDES_ALLOWLIST,
    _CONFIG_ALLOWED_KEYS,
    _CONFIG_PREFERENCE_KEYS,
    _CONFIG_MODEL_KEYS,
    _CONFIG_MODEL_LABELS,
    _CONFIG_PROBE_TIMEOUT_SECONDS,
    _CONFIG_PROBE_PROMPT,
    _CONFIG_WARMUP_TIMEOUT_SECONDS,
    _CONFIG_WARMUP_PROMPT,
    _deep_merge,
    _user_config_overrides,
    _build_runtime_config,
    _mask_secret_value,
    _mask_wecom_webhook,
    _warmup_model_names,
    _warmup_model_targets,
    _should_trigger_config_warmup,
    _build_pending_runtime_config,
    _should_probe_runtime_config,
    _probe_runtime_config,
    _invoke_runtime_warmup,
    _run_config_warmup,
    _config_response_for_user,
)

# Re-export job runtime helpers from api.job_runtime.
from api.job_runtime import (  # noqa: F401
    _job_store_instance,
    get_job_store,
    _background_tasks,
    _JOB_TIMEOUT,
    _create_tracked_task,
    _utcnow_iso,
    _set_job,
    _get_job,
    _emit_job_event,
    _attach_job_runtime_state,
    FIXED_TEAMS,
    ANALYST_ORDER,
    ANALYST_AGENT_NAMES,
    ANALYST_REPORT_MAP,
    AgentProgressTracker,
    _sse_pack,
    _stream_job_events,
    _require_job_owner,
)

# Re-export analysis runner from api.analysis_runner.
from api.analysis_runner import (  # noqa: F401
    _build_result_payload,
    _extract_message_text,
    _generate_tool_description,
    _run_job,
    _run_job_inner,
)

# Re-export user-context / scheduled-task helpers from api.context_helpers.
from api.context_helpers import (  # noqa: F401
    _resolve_scheduled_trade_date,
    _build_scheduled_analyze_request,
    _run_manual_trigger,
    _annotate_scheduled_with_imported_context,
    _merge_imported_user_context,
    _build_imported_user_context,
    _build_manual_imported_user_context,
    _extract_scheduled_update_kwargs,
    _get_horizon_analysts,
    _extract_request_user_context,
    _merge_user_context_payload,
    _compose_analysis_user_context,
    _apply_user_context_to_request,
    _extract_chat_text,
    _extract_symbol_and_date,
    _ai_extract_symbol_and_date_streaming,
    _ai_extract_symbol_and_date,
)

# Alias for backward compatibility with tests / routers that patch this name.
_run_scheduled_analysis_once = _run_manual_trigger

from api.database import SponsorDB, FeedbackDB, init_db
from api.database import get_db_ctx  # noqa: F401  (accessed lazily as api.main.get_db_ctx)

from tradingagents.default_config import DEFAULT_CONFIG  # noqa: F401  (re-exported for tests / patch targets)
from tradingagents.graph.data_collector import DataCollector

# 全局共享 DataCollector：同一 ticker+date 的数据只拉一次，所有 job 复用缓存
_shared_data_collector = DataCollector()


def _cors_allow_origins() -> list[str]:
    raw = os.getenv("CORS_ALLOW_ORIGINS", "").strip()
    default_origins = [
        "http://127.0.0.1:5174",
        "http://localhost:5174",
        "http://127.0.0.1:5175",
        "http://localhost:5175",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]
    if not raw:
        return default_origins
    return [item.strip() for item in raw.split(",") if item.strip()]


def _cors_allow_origin_regex() -> str | None:
    raw = os.getenv("CORS_ALLOW_ORIGIN_REGEX", "").strip()
    return raw or None


def _report_version_stats() -> None:
    """Report anonymous version stats to a configurable endpoint."""
    import threading, uuid

    url = os.getenv("VERSION_STATS_URL", "").strip()
    if not url:
        return

    def _send():
        try:
            requests.post(
                url,
                json={"v": APP_VERSION, "nonce": uuid.uuid4().hex},
                timeout=30,
            )
        except Exception as e:
            logger.debug(f"version stats post failed: {e}")

    threading.Thread(target=_send, daemon=True).start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize resources on startup and cleanup on shutdown."""
    init_db()
    _log("Database initialized.")
    store = get_job_store()
    store.clear()
    _background_tasks.clear()

    # Security: warn loudly if using default secret key
    if not os.getenv("TA_APP_SECRET_KEY"):
        _log("=" * 70)
        _log("WARNING: TA_APP_SECRET_KEY is not set!")
        _log("Using hardcoded default key. ALL encryption and JWT signing")
        _log("is INSECURE. Set TA_APP_SECRET_KEY env var before production use.")
        _log("=" * 70)

    _report_version_stats()
    # Pre-load trade calendar (uses mini_racer/V8 which is not thread-safe)
    from tradingagents.dataflows.trade_calendar import _load_cn_trade_dates
    _load_cn_trade_dates()
    _log("Trade calendar pre-loaded.")
    # Stock map is now lazy-loaded with local cache, no need to pre-load on startup
    _log("Stock map will be lazy-loaded from local cache on first use.")
    yield
    _log("Shutting down: Cleaning up resources...")
    _executor.shutdown(wait=True)
    _log("Executor shutdown complete.")


_is_prod = os.getenv("ENV", "").lower() == "prod"


def _get_version() -> str:
    """Get app version: APP_VERSION env > package metadata > 'dev'."""
    v = os.getenv("APP_VERSION")
    if v:
        return v
    try:
        from importlib.metadata import version as pkg_version
        return pkg_version("tradingagents")
    except Exception:
        return "dev"


APP_VERSION = _get_version()

app = FastAPI(
    title="TradingAgents-AShare API",
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url=None if _is_prod else "/docs",
    redoc_url=None if _is_prod else "/redoc",
    openapi_url=None if _is_prod else "/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins(),
    allow_origin_regex=_cors_allow_origin_regex(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_executor = ThreadPoolExecutor(max_workers=int(os.getenv("TA_MAX_WORKERS", "4")))


def _log(msg: str):
    """Helper to log with timestamp via standard logging."""
    logger.info(msg)


def _announcements_file() -> Path:
    return Path(__file__).resolve().parent / "announcements.json"


def _load_latest_announcement() -> Optional[Dict[str, Any]]:
    path = _announcements_file()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        _log(f"[Announcements] Failed to read {path.name}: {exc}")
        return None

    announcements = raw.get("announcements") if isinstance(raw, dict) else raw
    if not isinstance(announcements, list):
        return None

    for item in announcements:
        if not isinstance(item, dict):
            continue
        if item.get("active", True) is False:
            continue
        return item
    return None


def run() -> None:
    import uvicorn
    from pathlib import Path

    log_config = str(Path(__file__).parent / "logging_config.yaml")
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_config=log_config,
        timeout_keep_alive=900,       # 15分钟，匹配分析总耗时
        timeout_graceful_shutdown=30,
    )


def _sponsor_to_item(s: SponsorDB) -> SponsorItem:
    return SponsorItem(
        id=s.id,
        sponsor_type=s.sponsor_type,
        name=s.name,
        github=s.github,
        avatar=s.avatar,
        email=s.email,
        provider=s.provider,
        date=s.date,
    )


def _fb_to_item(fb: FeedbackDB) -> FeedbackItem:
    return FeedbackItem(
        id=fb.id,
        user_email=fb.user_email,
        subject=fb.subject,
        content=fb.content,
        admin_reply=fb.admin_reply,
        replied_at=fb.replied_at,
        is_read=fb.is_read,
        created_at=fb.created_at,
        updated_at=fb.updated_at,
    )


# ─── Register routers ───────────────────────────────────────────────────────

from api.routers import (
    analyze_router,
    auth_router,
    chip_deep_router,
    config_router,
    dashboard_router,
    feedback_router,
    jobs_router,
    market_router,
    misc_router,
    mount_static_files,
    portfolio_router,
    reports_router,
    scheduled_router,
    watchlist_router,
)

app.include_router(auth_router)
app.include_router(config_router)
app.include_router(market_router)
app.include_router(analyze_router)
app.include_router(jobs_router)
app.include_router(reports_router)
app.include_router(portfolio_router)
app.include_router(watchlist_router)
app.include_router(scheduled_router)
app.include_router(dashboard_router)
app.include_router(feedback_router)
app.include_router(chip_deep_router)
app.include_router(misc_router)

mount_static_files(app)

