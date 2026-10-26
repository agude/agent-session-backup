"""Report each host's last run from its .last-run.json record."""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent_session_backup import destination
from agent_session_backup.runner import LAST_RUN_NAME

DEFAULT_MAX_AGE = timedelta(hours=48)
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_DURATION = re.compile(r"(\d+)([smhd])")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_HEADER = (
    f"{'HOST':<12} {'FINISHED':<22} {'STATUS':<8} {'COPIED':>6} {'ERRORS':>6} {'AGE':>6}  RESULT"
)


def parse_duration(text: str) -> timedelta:
    """Parse a duration such as ``48h``. The units are s, m, h, and d."""
    match = _DURATION.fullmatch(text)
    if match is None:
        raise ValueError(f"expected a duration such as 48h, got {text!r}")
    return timedelta(seconds=int(match[1]) * _UNIT_SECONDS[match[2]])


@dataclass(frozen=True)
class HostRecord:
    """The fields of a valid .last-run.json record that status reports."""

    host: str
    finished: str
    status: str
    copied: int
    errors: int
    last_success: datetime | None


def report(dest_root: Path, *, now: datetime, max_age: timedelta = DEFAULT_MAX_AGE) -> int:
    """Print one row per host and return the exit status.

    Return 0 when every host is ok and fresh. Return 1 when a host is stale,
    partial, or unreadable, or when no host has a record. Return 2 when the
    destination is unavailable.
    """
    availability = destination.check(dest_root)
    if isinstance(availability, destination.Unavailable):
        print(f"destination: unavailable: {availability.reason}")
        return 2
    records = sorted(dest_root.glob(f"*/{LAST_RUN_NAME}"))
    if not records:
        print("no host has a last-run record")
        return 1
    print(_HEADER)
    failed = False
    for path in records:
        record = _read(path)
        if record is None:
            print(
                f"{path.parent.name:<12} {'-':<22} {'-':<8} {'-':>6} {'-':>6} {'-':>6}  UNREADABLE"
            )
            failed = True
            continue
        result = _result(record, now, max_age)
        failed = failed or result != "ok"
        print(_row(record, now, result))
    return 1 if failed else 0


def _result(record: HostRecord, now: datetime, max_age: timedelta) -> str:
    if record.status != "ok":
        return "partial"
    if record.last_success is None or now - record.last_success > max_age:
        return "STALE"
    return "ok"


def _row(record: HostRecord, now: datetime, result: str) -> str:
    return (
        f"{record.host:<12} {record.finished:<22} {record.status:<8} "
        f"{record.copied:>6} {record.errors:>6} {_age(now, record.last_success):>6}  {result}"
    )


def _age(now: datetime, last_success: datetime | None) -> str:
    if last_success is None:
        return "never"
    return f"{int((now - last_success).total_seconds() // 3600)}h"


def _read(path: Path) -> HostRecord | None:
    try:
        return _load(path)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _load(path: Path) -> HostRecord:
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    status = data["status"]
    finished = data["finished"]
    counts = data["counts"]
    if (
        status not in {"ok", "partial"}
        or not isinstance(finished, str)
        or not isinstance(counts, dict)
    ):
        raise ValueError("unexpected field types")
    return HostRecord(
        host=path.parent.name,
        finished=finished,
        status=status,
        copied=int(counts["copied"]),
        errors=int(counts["errors"]),
        last_success=_parse_time(data["last_success"]),
    )


def _parse_time(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("last_success must be a timestamp or null")
    return datetime.strptime(value, TIMESTAMP_FORMAT).replace(tzinfo=UTC)
