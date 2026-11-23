#!/usr/bin/env python3
"""Run local VPS health checks and send WeCom alerts on state transitions."""

import argparse
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STATE_FILE = Path(os.environ.get("TA_MONITOR_STATE_FILE", "/var/lib/tradingagents/monitor-state.json"))
DEFAULT_ENV_FILE = Path("/opt/tradingagents/monitor.env")
DEFAULT_HEARTBEAT_FILE = Path("/var/lib/tradingagents/data/scheduler_heartbeat.json")
DEFAULT_BACKUP_DIR = Path("/var/lib/tradingagents/backups")


class MonitorError(RuntimeError):
    pass


@dataclass
class MonitorConfig:
    base_url: str = "http://127.0.0.1:8000"
    heartbeat_file: Path = DEFAULT_HEARTBEAT_FILE
    backup_dir: Path = DEFAULT_BACKUP_DIR
    disk_path: Path = Path("/var/lib/tradingagents")
    meminfo_path: Path = Path("/proc/meminfo")
    env_file: Path = DEFAULT_ENV_FILE
    state_file: Path = DEFAULT_STATE_FILE
    request_timeout: float = 5.0
    heartbeat_max_age: float = 180.0
    backup_max_age: float = 36 * 60 * 60
    disk_threshold: float = 85.0
    memory_threshold: float = 90.0


def _utc_now(now: datetime | None = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _parse_timestamp(value) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    if not isinstance(value, str):
        raise MonitorError("heartbeat timestamp must be an ISO timestamp or Unix epoch")
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MonitorError("heartbeat timestamp is invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def heartbeat_age_seconds(path: Path, now: datetime | None = None) -> float:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MonitorError(f"cannot read scheduler heartbeat: {exc}") from exc
    if not isinstance(payload, dict):
        raise MonitorError("scheduler heartbeat must be a JSON object")
    for key in ("timestamp", "heartbeat_at", "updated_at", "time"):
        if key in payload:
            timestamp = _parse_timestamp(payload[key])
            break
    else:
        raise MonitorError("scheduler heartbeat has no timestamp")
    age = (_utc_now(now) - timestamp).total_seconds()
    if age < 0:
        raise MonitorError("scheduler heartbeat timestamp is in the future")
    return age


def check_heartbeat(path: Path, now: datetime | None = None, max_age: float = 180.0) -> str | None:
    try:
        age = heartbeat_age_seconds(path, now)
    except (MonitorError, ValueError) as exc:
        return f"scheduler heartbeat check failed: {exc}"
    if age > max_age:
        return f"scheduler heartbeat is stale: {age:.0f}s > {max_age:.0f}s"
    return None


def latest_backup_age_seconds(backup_dir: Path, now: datetime | None = None) -> float:
    backups = [path for path in Path(backup_dir).glob("*.db") if path.is_file()]
    if not backups:
        raise MonitorError(f"no database backups found in {backup_dir}")
    latest = max(path.stat().st_mtime for path in backups)
    age = _utc_now(now).timestamp() - latest
    if age < 0:
        raise MonitorError("latest backup timestamp is in the future")
    return age


def check_latest_backup(
    backup_dir: Path,
    now: datetime | None = None,
    max_age: float = 36 * 60 * 60,
) -> str | None:
    try:
        age = latest_backup_age_seconds(backup_dir, now)
    except (MonitorError, OSError) as exc:
        return f"backup check failed: {exc}"
    if age > max_age:
        return f"latest backup is stale: {age / 3600:.1f}h > {max_age / 3600:.1f}h"
    return None


def check_readyz(base_url: str, timeout: float = 5.0) -> str | None:
    url = f"{base_url.rstrip('/')}/readyz"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            status = response.getcode()
            response.read(1024)
    except urllib.error.HTTPError as exc:
        return f"readyz returned HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return f"readyz request failed: {exc}"
    if status != 200:
        return f"readyz returned HTTP {status}"
    return None


def memory_usage_percent(meminfo_path: Path = Path("/proc/meminfo")) -> float:
    values = {}
    try:
        for line in Path(meminfo_path).read_text(encoding="utf-8").splitlines():
            key, separator, raw_value = line.partition(":")
            if separator and key in {"MemTotal", "MemAvailable"}:
                values[key] = float(raw_value.strip().split()[0])
    except (OSError, ValueError, IndexError) as exc:
        raise MonitorError(f"cannot read memory usage: {exc}") from exc
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if not total or total <= 0 or available is None:
        raise MonitorError("MemTotal or MemAvailable is missing from meminfo")
    return (total - available) / total * 100


def collect_issues(config: MonitorConfig, now: datetime | None = None) -> list[str]:
    issues = []
    for result in (
        check_readyz(config.base_url, config.request_timeout),
        check_heartbeat(config.heartbeat_file, now, config.heartbeat_max_age),
        check_latest_backup(config.backup_dir, now, config.backup_max_age),
    ):
        if result:
            issues.append(result)

    try:
        disk_usage = shutil.disk_usage(config.disk_path)
        if disk_usage.total <= 0:
            raise MonitorError("disk total is zero")
        disk_percent = disk_usage.used / disk_usage.total * 100
        if disk_percent >= config.disk_threshold:
            issues.append(f"disk usage is high: {disk_percent:.1f}% >= {config.disk_threshold:.1f}%")
    except (OSError, MonitorError) as exc:
        issues.append(f"disk usage check failed: {exc}")

    try:
        memory_percent = memory_usage_percent(config.meminfo_path)
        if memory_percent >= config.memory_threshold:
            issues.append(f"memory usage is high: {memory_percent:.1f}% >= {config.memory_threshold:.1f}%")
    except MonitorError as exc:
        issues.append(str(exc))
    return issues


def _read_state(path: Path) -> dict | None:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict) or state.get("status") not in {"healthy", "unhealthy"}:
        return None
    issues = state.get("issues")
    if not isinstance(issues, list) or not all(isinstance(issue, str) for issue in issues):
        return None
    return {"status": state["status"], "issues": sorted(issues)}


def _write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def should_notify(previous: dict | None, current: dict) -> bool:
    if previous is None:
        return current["status"] == "unhealthy"
    return previous["status"] != current["status"] or previous["issues"] != current["issues"]


def process_state(state_file: Path, issues: list[str], webhook_url: str | None = None, notifier=None) -> bool:
    state_file = Path(state_file)
    previous = _read_state(state_file)
    current = {"status": "unhealthy" if issues else "healthy", "issues": sorted(issues)}
    notified = False
    if should_notify(previous, current) and webhook_url:
        if notifier is None:
            notifier = send_wecom
        if current["status"] == "healthy":
            message = "TradingAgents VPS 监控恢复\n所有检查已恢复正常。"
        else:
            message = "TradingAgents VPS 监控告警\n" + "\n".join(current["issues"])
        notifier(webhook_url, message)
        notified = True
    _write_state(state_file, current)
    return notified


def load_webhook(env_file: Path) -> str | None:
    try:
        lines = Path(env_file).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    values = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        key, separator, value = stripped.partition("=")
        if separator:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            values[key.strip()] = value
    for key in ("WECOM_WEBHOOK_URL", "WECOM_WEBHOOK", "WECHAT_WEBHOOK_URL"):
        if values.get(key):
            return values[key]
    return None


def send_wecom(webhook_url: str, message: str, timeout: float = 5.0) -> None:
    payload = json.dumps({"msgtype": "text", "text": {"content": message}}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(webhook_url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(64 * 1024)
        if response.getcode() != 200:
            raise MonitorError(f"WeCom notification returned HTTP {response.getcode()}")
    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        raise MonitorError("WeCom notification returned invalid JSON") from exc
    if result.get("errcode") != 0:
        raise MonitorError(f"WeCom notification failed with errcode {result.get('errcode')}")


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--heartbeat-file", type=Path, default=DEFAULT_HEARTBEAT_FILE)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    parser.add_argument("--disk-path", type=Path, default=Path("/var/lib/tradingagents"))
    parser.add_argument("--meminfo-path", type=Path, default=Path("/proc/meminfo"))
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    parser.add_argument("--request-timeout", type=positive_float, default=5.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = MonitorConfig(
        base_url=args.base_url,
        heartbeat_file=args.heartbeat_file,
        backup_dir=args.backup_dir,
        disk_path=args.disk_path,
        meminfo_path=args.meminfo_path,
        env_file=args.env_file,
        state_file=args.state_file,
        request_timeout=args.request_timeout,
    )
    issues = collect_issues(config)
    for issue in issues:
        print(issue, file=sys.stderr)
    try:
        webhook = load_webhook(config.env_file)
        process_state(config.state_file, issues, webhook)
    except (MonitorError, OSError, ValueError, urllib.error.URLError) as exc:
        print(f"monitor state or notification failed: {exc}", file=sys.stderr)
        return 1
    if issues:
        if not webhook:
            print("WeCom webhook is not configured; alert was not sent", file=sys.stderr)
        return 1
    print("ok: all VPS monitor checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
