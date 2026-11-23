import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from api.scheduler_heartbeat import (
    scheduler_heartbeat_age_seconds,
    scheduler_heartbeat_status,
    write_scheduler_heartbeat,
)


def test_scheduler_heartbeat_is_fresh_and_stale_by_age(tmp_path, monkeypatch):
    heartbeat = tmp_path / "state" / "scheduler_heartbeat.json"
    monkeypatch.setenv("SCHEDULER_HEARTBEAT_FILE", str(heartbeat))
    now = datetime.now(timezone.utc)
    write_scheduler_heartbeat(now=now)

    assert scheduler_heartbeat_age_seconds(now=now + timedelta(seconds=60)) == 60
    fresh = scheduler_heartbeat_status()
    assert fresh["status"] == "ok"

    old = now - timedelta(seconds=181)
    write_scheduler_heartbeat(now=old)
    stale = scheduler_heartbeat_status()
    assert stale["status"] == "stale"
    assert stale["age_seconds"] >= 180


def test_scheduler_heartbeat_missing_and_invalid(tmp_path, monkeypatch):
    heartbeat = tmp_path / "scheduler_heartbeat.json"
    monkeypatch.setenv("SCHEDULER_HEARTBEAT_FILE", str(heartbeat))
    assert scheduler_heartbeat_status()["status"] == "missing"

    heartbeat.write_text("not-json", encoding="utf-8")
    assert scheduler_heartbeat_status()["status"] == "invalid"


def test_readyz_reports_database_and_scheduler_status(tmp_path, monkeypatch):
    from api.routers.misc import readyz

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("TA_REQUIRE_SCHEDULER", "1")
    monkeypatch.setenv("SCHEDULER_HEARTBEAT_FILE", str(tmp_path / "scheduler_heartbeat.json"))
    write_scheduler_heartbeat()

    db = MagicMock()
    response = readyz(db)
    payload = json.loads(response.body)
    assert response.status_code == 200
    assert payload["checks"]["database"]["status"] == "ok"
    assert payload["checks"]["scheduler"]["status"] == "ok"


def test_readyz_fails_when_scheduler_heartbeat_is_missing(tmp_path, monkeypatch):
    from api.routers.misc import readyz

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("TA_REQUIRE_SCHEDULER", "1")
    monkeypatch.setenv("SCHEDULER_HEARTBEAT_FILE", str(tmp_path / "missing.json"))

    response = readyz(MagicMock())
    payload = json.loads(response.body)
    assert response.status_code == 503
    assert payload["status"] == "not_ready"
    assert payload["checks"]["scheduler"]["status"] == "missing"


def test_readyz_fails_when_database_is_unavailable(monkeypatch):
    from api.routers.misc import readyz

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("TA_REQUIRE_SCHEDULER", raising=False)
    db = MagicMock()
    db.execute.side_effect = RuntimeError("database unavailable")

    response = readyz(db)
    payload = json.loads(response.body)
    assert response.status_code == 503
    assert payload["checks"]["database"] == {"status": "error", "error": "RuntimeError"}
