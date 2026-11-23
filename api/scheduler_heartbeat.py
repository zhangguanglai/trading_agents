"""Shared scheduler heartbeat persistence and freshness checks."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_HEARTBEAT_FILE = Path("data") / "scheduler_heartbeat.json"
DEFAULT_MAX_AGE_SECONDS = 180


def scheduler_heartbeat_path() -> Path:
    return Path(os.getenv("SCHEDULER_HEARTBEAT_FILE", str(DEFAULT_HEARTBEAT_FILE)))


def write_scheduler_heartbeat(*, now: datetime | None = None) -> dict[str, Any]:
    """Atomically record that the scheduler event loop is still running."""
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    payload = {
        "timestamp": timestamp.astimezone(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "version": os.getenv("APP_VERSION", "dev"),
    }
    path = scheduler_heartbeat_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return payload


def scheduler_heartbeat_age_seconds(*, now: datetime | None = None) -> float:
    """Return heartbeat age, raising for a missing or malformed heartbeat."""
    payload = json.loads(scheduler_heartbeat_path().read_text(encoding="utf-8"))
    raw_timestamp = payload["timestamp"]
    timestamp = datetime.fromisoformat(str(raw_timestamp).replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0.0, (current - timestamp.astimezone(timezone.utc)).total_seconds())


def scheduler_heartbeat_status(max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS) -> dict[str, Any]:
    try:
        age = scheduler_heartbeat_age_seconds()
    except FileNotFoundError:
        return {"status": "missing"}
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return {"status": "invalid"}
    return {
        "status": "ok" if age <= max_age_seconds else "stale",
        "age_seconds": round(age, 3),
        "max_age_seconds": max_age_seconds,
    }


def main() -> int:
    import logging

    status = scheduler_heartbeat_status()
    logging.getLogger(__name__).info("Scheduler heartbeat: %s", status)
    return 0 if status["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
