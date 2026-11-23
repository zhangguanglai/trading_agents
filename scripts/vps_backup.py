#!/usr/bin/env python3
"""Create and retain consistent SQLite backups using only the standard library."""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SOURCE = Path("/var/lib/tradingagents/data/tradingagents.db")
DEFAULT_BACKUP_DIR = Path("/var/lib/tradingagents/backups")
BACKUP_PREFIX = "tradingagents-"
BACKUP_NAME = re.compile(r"^tradingagents-(\d{8}T\d{6}\.\d{6}Z)\.db$")


class BackupError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(path: Path, payload: dict) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def create_backup(source: Path, backup_dir: Path) -> Path:
    source = Path(source)
    backup_dir = Path(backup_dir)
    if not source.is_file():
        raise FileNotFoundError(f"source database does not exist: {source}")

    backup_dir.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc)
    final_path = backup_dir / f"{BACKUP_PREFIX}{created_at:%Y%m%dT%H%M%S.%fZ}.db"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{final_path.name}.", suffix=".tmp", dir=backup_dir)
    os.close(descriptor)
    temporary = Path(temporary_name)
    metadata_path = Path(f"{final_path}.meta.json")
    published = False

    try:
        source_uri = f"{source.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True)) as source_connection:
            with closing(sqlite3.connect(temporary)) as destination_connection:
                source_connection.backup(destination_connection)
                destination_connection.commit()
                integrity_rows = destination_connection.execute("PRAGMA integrity_check").fetchall()

        if integrity_rows != [("ok",)]:
            details = "; ".join(str(row[0]) for row in integrity_rows)
            raise BackupError(f"backup integrity_check failed: {details}")

        digest = sha256_file(temporary)
        os.replace(temporary, final_path)
        published = True
        _atomic_write_json(
            metadata_path,
            {
                "backup_file": final_path.name,
                "created_at": created_at.isoformat().replace("+00:00", "Z"),
                "integrity_check": "ok",
                "sha256": digest,
                "size_bytes": final_path.stat().st_size,
                "source": str(source),
            },
        )
        return final_path
    except Exception:
        temporary.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        if published:
            final_path.unlink(missing_ok=True)
        raise


def _backup_timestamp(path: Path) -> datetime | None:
    match = BACKUP_NAME.match(path.name)
    if match is None:
        return None
    return datetime.strptime(match.group(1), "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=timezone.utc)


def select_backups_to_keep(paths: list[Path], retention_days: int, retention_weeks: int) -> set[Path]:
    if retention_days < 0 or retention_weeks < 0:
        raise ValueError("retention values must be non-negative")

    records = []
    for path in paths:
        timestamp = _backup_timestamp(Path(path))
        if timestamp is not None:
            records.append((Path(path), timestamp))
    records.sort(key=lambda item: (item[1], item[0].name), reverse=True)

    newest_by_day = {}
    newest_by_week = {}
    for path, timestamp in records:
        newest_by_day.setdefault(timestamp.date(), path)
        iso = timestamp.isocalendar()
        newest_by_week.setdefault((iso.year, iso.week), path)

    keep = set()
    for day in sorted(newest_by_day, reverse=True)[:retention_days]:
        keep.add(newest_by_day[day])
    for week in sorted(newest_by_week, reverse=True)[:retention_weeks]:
        keep.add(newest_by_week[week])
    return keep


def prune_backups(backup_dir: Path, retention_days: int = 14, retention_weeks: int = 8) -> list[Path]:
    backup_dir = Path(backup_dir)
    paths = [path for path in backup_dir.glob(f"{BACKUP_PREFIX}*.db") if _backup_timestamp(path) is not None]
    keep = select_backups_to_keep(paths, retention_days, retention_weeks)
    deleted = []
    for path in paths:
        if path in keep:
            continue
        path.unlink(missing_ok=True)
        Path(f"{path}.meta.json").unlink(missing_ok=True)
        deleted.append(path)
    return deleted


def perform_backup(
    source: Path = DEFAULT_SOURCE,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    retention_days: int = 14,
    retention_weeks: int = 8,
) -> Path | None:
    source = Path(source)
    if not source.exists():
        print(f"skipped: source database does not exist: {source}")
        return None
    backup = create_backup(source, backup_dir)
    prune_backups(backup_dir, retention_days, retention_weeks)
    print(f"created: {backup}")
    return backup


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    parser.add_argument("--retention-days", type=positive_int, default=14)
    parser.add_argument("--retention-weeks", type=positive_int, default=8)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        perform_backup(args.source, args.backup_dir, args.retention_days, args.retention_weeks)
    except (BackupError, OSError, sqlite3.Error, ValueError) as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
