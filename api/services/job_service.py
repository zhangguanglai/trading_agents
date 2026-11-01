"""Compatibility facade for scheduler imports.

The standalone scheduler needs access to the same job store, job runner,
and scheduled-analysis helpers used by the API. This module re-exports
these symbols from ``api.main`` so the scheduler depends on a stable
service-layer interface rather than the routing module directly.
"""

from __future__ import annotations

# These symbols live in api.main because they depend on global API state
# (job store, executor, shared data collector, runtime config, etc.).
# Re-exporting them here lets scheduler/main.py import from this service
# module while keeping the concrete implementations and global state in
# api.main as required.
from api.main import (
    _build_imported_user_context,
    _build_scheduled_analyze_request,
    _emit_job_event,
    _get_job,
    _resolve_scheduled_trade_date,
    _run_job,
    _set_job,
    get_job_store,
)

__all__ = [
    "_build_imported_user_context",
    "_build_scheduled_analyze_request",
    "_emit_job_event",
    "_get_job",
    "_resolve_scheduled_trade_date",
    "_run_job",
    "_set_job",
    "get_job_store",
]
