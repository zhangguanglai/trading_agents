import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


smoke = load_script("smoke")
vps_backup = load_script("vps_backup")
vps_monitor = load_script("vps_monitor")


def create_source_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    connection.executemany("INSERT INTO items(value) VALUES (?)", [("alpha",), ("beta",)])
    connection.commit()
    return connection


def test_backup_preserves_wal_content_and_writes_verified_metadata(tmp_path: Path):
    source = tmp_path / "data" / "tradingagents.db"
    source.parent.mkdir()
    connection = create_source_database(source)
    try:
        backup = vps_backup.create_backup(source, tmp_path / "backups")
    finally:
        connection.close()

    copied = sqlite3.connect(backup)
    try:
        assert copied.execute("SELECT value FROM items ORDER BY id").fetchall() == [("alpha",), ("beta",)]
        assert copied.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        copied.close()

    metadata = json.loads(Path(f"{backup}.meta.json").read_text(encoding="utf-8"))
    assert metadata["sha256"] == hashlib.sha256(backup.read_bytes()).hexdigest()
    assert metadata["size_bytes"] == backup.stat().st_size
    assert metadata["integrity_check"] == "ok"


def test_backup_skip_missing_and_reject_corrupt_source(tmp_path: Path):
    missing = tmp_path / "missing.db"
    backup_dir = tmp_path / "backups"
    assert vps_backup.perform_backup(missing, backup_dir) is None
    assert vps_backup.main(["--source", str(missing), "--backup-dir", str(backup_dir)]) == 0
    assert not backup_dir.exists()

    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"not a sqlite database" * 4)
    with pytest.raises((sqlite3.DatabaseError, vps_backup.BackupError)):
        vps_backup.create_backup(corrupt, backup_dir)
    assert not list(backup_dir.glob("*.db"))
    assert not list(backup_dir.glob("*.tmp"))
    assert vps_backup.main(["--source", str(corrupt), "--backup-dir", str(backup_dir)]) == 1


def test_backup_retention_combines_daily_and_weekly_snapshots(tmp_path: Path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    dates = [
        datetime(2026, 1, 5, tzinfo=timezone.utc),
        datetime(2026, 1, 6, tzinfo=timezone.utc),
        datetime(2026, 1, 12, tzinfo=timezone.utc),
        datetime(2026, 1, 13, tzinfo=timezone.utc),
        datetime(2026, 1, 19, tzinfo=timezone.utc),
        datetime(2026, 1, 20, tzinfo=timezone.utc),
    ]
    paths = {}
    for date in dates:
        path = backup_dir / f"tradingagents-{date:%Y%m%dT%H%M%S}.000000Z.db"
        path.write_bytes(b"backup")
        Path(f"{path}.meta.json").write_text("{}", encoding="utf-8")
        paths[date] = path

    vps_backup.prune_backups(backup_dir, retention_days=1, retention_weeks=2)

    assert paths[dates[-1]].exists()
    assert paths[datetime(2026, 1, 13, tzinfo=timezone.utc)].exists()
    assert all(not paths[date].exists() for date in dates[:-1] if date.day != 13)
    assert not Path(f"{paths[dates[0]]}.meta.json").exists()


def test_heartbeat_and_latest_backup_age(tmp_path: Path):
    now = datetime(2026, 1, 20, 12, 0, tzinfo=timezone.utc)
    heartbeat = tmp_path / "scheduler_heartbeat.json"
    timestamp = (now - timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
    heartbeat.write_text(json.dumps({"timestamp": timestamp}), encoding="utf-8")
    assert vps_monitor.heartbeat_age_seconds(heartbeat, now=now) == 60
    assert vps_monitor.check_heartbeat(heartbeat, now=now) is None
    assert "heartbeat" in vps_monitor.check_heartbeat(heartbeat, now=now, max_age=30)

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    old = backup_dir / "old.db"
    new = backup_dir / "new.db"
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    os.utime(old, ((now - timedelta(hours=5)).timestamp(),) * 2)
    os.utime(new, ((now - timedelta(hours=1)).timestamp(),) * 2)
    assert vps_monitor.latest_backup_age_seconds(backup_dir, now=now) == pytest.approx(3600)
    assert vps_monitor.check_latest_backup(backup_dir, now=now) is None
    assert "backup" in vps_monitor.check_latest_backup(backup_dir, now=now, max_age=1800)


def test_memory_usage_and_monitor_notification_transitions(tmp_path: Path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 1000 kB\nMemAvailable: 100 kB\n", encoding="utf-8")
    assert vps_monitor.memory_usage_percent(meminfo) == 90

    state_file = tmp_path / "monitor-state.json"
    sent = []

    def notifier(url: str, message: str):
        sent.append((url, message))

    assert not vps_monitor.process_state(state_file, [], "https://example.invalid", notifier)
    assert vps_monitor.process_state(state_file, ["readyz failed"], "https://example.invalid", notifier)
    assert not vps_monitor.process_state(state_file, ["readyz failed"], "https://example.invalid", notifier)
    assert vps_monitor.process_state(
        state_file,
        ["backup is stale", "readyz failed"],
        "https://example.invalid",
        notifier,
    )
    assert vps_monitor.process_state(state_file, [], "https://example.invalid", notifier)
    assert len(sent) == 3
    assert "恢复" in sent[-1][1]
    assert not vps_monitor.process_state(state_file, [], "https://example.invalid", notifier)


def test_notification_failure_does_not_commit_state(tmp_path: Path):
    state_file = tmp_path / "monitor-state.json"

    def failing_notifier(url: str, message: str):
        raise vps_monitor.MonitorError("notification failed")

    with pytest.raises(vps_monitor.MonitorError):
        vps_monitor.process_state(state_file, ["disk usage is high"], "https://example.invalid", failing_notifier)
    assert not state_file.exists()


class SmokeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return None

    def getcode(self):
        return 200

    def read(self, limit):
        return b"ok"


def install_smoke_clock(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock["now"])

    def advance(seconds):
        clock["now"] += seconds

    monkeypatch.setattr(smoke.time, "sleep", advance)


def test_smoke_retries_until_every_endpoint_passes(monkeypatch):
    install_smoke_clock(monkeypatch)
    attempts = []

    def opener(url, timeout):
        attempts.append(url)
        if url.endswith("/readyz") and attempts.count(url) == 1:
            raise urllib.error.URLError("starting")
        return SmokeResponse()

    errors = smoke.run_smoke("http://127.0.0.1:8000", 5, opener)

    assert errors == {}
    assert set(attempts) == {
        "http://127.0.0.1:8000/healthz",
        "http://127.0.0.1:8000/readyz",
        "http://127.0.0.1:8000/",
    }
    assert sum(url.endswith("/readyz") for url in attempts) == 2


def test_smoke_returns_all_pending_endpoints_after_timeout(monkeypatch):
    install_smoke_clock(monkeypatch)

    def unavailable(url, timeout):
        raise urllib.error.URLError("unavailable")

    errors = smoke.run_smoke("http://127.0.0.1:8000", 2, unavailable)

    assert set(errors) == set(smoke.ENDPOINTS)
    assert all("unavailable" in error for error in errors.values())


posix_only = pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason="requires Bash and flock")
OLD_IMAGE = f"ghcr.io/example/tradingagents-ashare@sha256:{'a' * 64}"
NEW_IMAGE = f"ghcr.io/example/tradingagents-ashare@sha256:{'b' * 64}"


def prepare_deploy_fixture(tmp_path: Path):
    deploy_dir = tmp_path / "deploy"
    data_dir = tmp_path / "data"
    fake_bin = tmp_path / "bin"
    deploy_dir.mkdir()
    data_dir.mkdir()
    fake_bin.mkdir()
    (deploy_dir / ".env").write_text("TA_APP_SECRET_KEY=test\n", encoding="utf-8")
    (deploy_dir / "docker-compose.split.yml").write_text("services: {}\n", encoding="utf-8")
    call_log = tmp_path / "calls.log"

    docker = fake_bin / "docker"
    docker.write_text(
        '#!/bin/sh\nprintf \'docker %s\\n\' "$*" >> "$CALL_LOG"\nexit 0\n',
        encoding="utf-8",
    )
    python = fake_bin / "python3"
    python.write_text(
        "#!/bin/sh\n"
        'printf \'python3 %s\\n\' "$*" >> "$CALL_LOG"\n'
        'case "$1" in\n'
        "  *vps_backup.py)\n"
        '    if [ "${BACKUP_FAIL:-0}" = 1 ]; then exit 1; fi ;;\n'
        "  *smoke.py)\n"
        "    release=''\n"
        '    if [ -r "$TA_DEPLOY_DIR/release.env" ]; then IFS= read -r release < "$TA_DEPLOY_DIR/release.env"; fi\n'
        '    printf \'smoke %s\\n\' "$release" >> "$CALL_LOG"\n'
        '    if [ "${SMOKE_FAIL:-0}" = 1 ]; then exit 1; fi\n'
        '    if [ "${SMOKE_FAIL_ONCE:-0}" = 1 ] && [ ! -f "$TEST_TMP/smoke.failed" ]; then\n'
        '      : > "$TEST_TMP/smoke.failed"\n'
        "      exit 1\n"
        "    fi ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    python.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "CALL_LOG": str(call_log),
            "TEST_TMP": str(tmp_path),
            "TA_DEPLOY_DIR": str(deploy_dir),
            "TA_DATA_DIR": str(data_dir),
        }
    )
    return {"deploy_dir": deploy_dir, "call_log": call_log, "env": env}


def run_deploy(fixture, *args):
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / "vps_deploy.sh"), *args],
        cwd=ROOT,
        env=fixture["env"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


@posix_only
@pytest.mark.parametrize(
    "image_ref",
    [
        "latest",
        "ghcr.io/example/tradingagents-ashare:latest",
        "ghcr.io/example/tradingagents-ashare@sha256:abc",
        f"ghcr.io/example/tradingagents-ashare@sha256:{'A' * 64}",
    ],
)
def test_deploy_rejects_non_immutable_image_references(tmp_path: Path, image_ref):
    result = run_deploy(prepare_deploy_fixture(tmp_path), "deploy", image_ref)

    assert result.returncode != 0
    assert "expected repository@sha256" in result.stderr


@posix_only
def test_first_deployment_uses_requested_digest(tmp_path: Path):
    fixture = prepare_deploy_fixture(tmp_path)

    result = run_deploy(fixture, "deploy", NEW_IMAGE)

    assert result.returncode == 0, result.stderr
    assert (fixture["deploy_dir"] / "release.env").read_text(encoding="utf-8") == f"IMAGE_REF={NEW_IMAGE}\n"
    assert not (fixture["deploy_dir"] / "previous-release.env").exists()
    assert f"smoke IMAGE_REF={NEW_IMAGE}" in fixture["call_log"].read_text(encoding="utf-8")


@posix_only
def test_failed_smoke_restores_previous_digest_and_reports_failure(tmp_path: Path):
    fixture = prepare_deploy_fixture(tmp_path)
    (fixture["deploy_dir"] / "release.env").write_text(f"IMAGE_REF={OLD_IMAGE}\n", encoding="utf-8")
    fixture["env"]["SMOKE_FAIL_ONCE"] = "1"

    result = run_deploy(fixture, "deploy", NEW_IMAGE)

    assert result.returncode != 0
    assert (fixture["deploy_dir"] / "release.env").read_text(encoding="utf-8") == f"IMAGE_REF={OLD_IMAGE}\n"
    log = fixture["call_log"].read_text(encoding="utf-8")
    assert log.index(f"smoke IMAGE_REF={NEW_IMAGE}") < log.index(f"smoke IMAGE_REF={OLD_IMAGE}")
    assert "Automatic rollback passed smoke checks; the original deployment still failed." in result.stdout


@posix_only
def test_failed_first_deployment_has_no_automatic_rollback(tmp_path: Path):
    fixture = prepare_deploy_fixture(tmp_path)
    fixture["env"]["SMOKE_FAIL"] = "1"

    result = run_deploy(fixture, "deploy", NEW_IMAGE)

    assert result.returncode != 0
    assert not (fixture["deploy_dir"] / "previous-release.env").exists()
    assert "Attempting automatic rollback" not in result.stdout


@posix_only
def test_manual_rollback_uses_previous_digest(tmp_path: Path):
    fixture = prepare_deploy_fixture(tmp_path)
    (fixture["deploy_dir"] / "release.env").write_text(f"IMAGE_REF={NEW_IMAGE}\n", encoding="utf-8")
    (fixture["deploy_dir"] / "previous-release.env").write_text(f"IMAGE_REF={OLD_IMAGE}\n", encoding="utf-8")

    result = run_deploy(fixture, "rollback")

    assert result.returncode == 0, result.stderr
    assert (fixture["deploy_dir"] / "release.env").read_text(encoding="utf-8") == f"IMAGE_REF={OLD_IMAGE}\n"
    assert f"smoke IMAGE_REF={OLD_IMAGE}" in fixture["call_log"].read_text(encoding="utf-8")


@posix_only
def test_backup_failure_does_not_change_release(tmp_path: Path):
    fixture = prepare_deploy_fixture(tmp_path)
    (fixture["deploy_dir"] / "release.env").write_text(f"IMAGE_REF={OLD_IMAGE}\n", encoding="utf-8")
    fixture["env"]["BACKUP_FAIL"] = "1"

    result = run_deploy(fixture, "deploy", NEW_IMAGE)

    assert result.returncode != 0
    assert (fixture["deploy_dir"] / "release.env").read_text(encoding="utf-8") == f"IMAGE_REF={OLD_IMAGE}\n"
    assert not (fixture["deploy_dir"] / "previous-release.env").exists()
    assert "Pulling" not in result.stdout


def test_unhealthy_monitor_without_webhook_exits_nonzero(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(vps_monitor, "collect_issues", lambda config: ["readyz failed"])
    exit_code = vps_monitor.main(
        [
            "--env-file",
            str(tmp_path / "missing-monitor.env"),
            "--state-file",
            str(tmp_path / "monitor-state.json"),
        ]
    )
    assert exit_code == 1
