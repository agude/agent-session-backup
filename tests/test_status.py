"""Tests for the status command."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_session_backup import destination
from agent_session_backup.cli import main
from agent_session_backup.status import DEFAULT_MAX_AGE, parse_duration, report

NOW = datetime(2026, 10, 28, 18, 0, 0, tzinfo=UTC)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _record(
    host: str,
    finished: datetime,
    *,
    result_status: str = "ok",
    last_success: datetime | None = None,
) -> dict[str, object]:
    return {
        "host": host,
        "version": "0.0.0",
        "started": _stamp(finished),
        "finished": _stamp(finished),
        "status": result_status,
        "last_success": _stamp(last_success or finished),
        "counts": {"copied": 0, "preserved": 0, "skipped": 0, "errors": 0},
        "bytes_copied": 0,
        "errors": [],
    }


def _write(dest: Path, host: str, content: str) -> None:
    path = dest / host / ".last-run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _destination(tmp_path: Path) -> Path:
    dest = tmp_path / "nas"
    dest.mkdir()
    (dest / destination.SENTINEL_NAME).write_text("{}\n", encoding="utf-8")
    return dest


MALFORMED = [
    "not json",
    json.dumps(["a", "list"]),
    json.dumps({"status": "ok"}),
    json.dumps(_record("dirac", NOW, result_status="weird")),
    json.dumps({**_record("dirac", NOW), "counts": "many"}),
    json.dumps({**_record("dirac", NOW), "last_success": 5}),
    json.dumps({**_record("dirac", NOW), "last_success": "yesterday"}),
]


def test_fresh_hosts_exit_zero_and_are_all_listed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = _destination(tmp_path)
    _write(dest, "dirac", json.dumps(_record("dirac", NOW - timedelta(hours=1))))
    _write(dest, "einstein", json.dumps(_record("einstein", NOW - timedelta(hours=2))))
    assert report(dest, now=NOW) == 0
    output = capsys.readouterr().out
    assert "dirac" in output
    assert "einstein" in output
    assert "STALE" not in output


def test_host_older_than_max_age_is_stale_and_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = _destination(tmp_path)
    _write(dest, "dirac", json.dumps(_record("dirac", NOW - timedelta(hours=49))))
    assert report(dest, now=NOW) == 1
    assert "STALE" in capsys.readouterr().out


def test_longer_max_age_makes_the_same_host_fresh(tmp_path: Path) -> None:
    dest = _destination(tmp_path)
    _write(dest, "dirac", json.dumps(_record("dirac", NOW - timedelta(hours=49))))
    assert report(dest, now=NOW, max_age=timedelta(hours=72)) == 0


def test_default_max_age_is_48_hours() -> None:
    assert timedelta(hours=48) == DEFAULT_MAX_AGE


def test_partial_host_exits_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dest = _destination(tmp_path)
    partial = _record(
        "dirac",
        NOW - timedelta(hours=1),
        result_status="partial",
        last_success=NOW - timedelta(hours=1),
    )
    _write(dest, "dirac", json.dumps(partial))
    assert report(dest, now=NOW) == 1
    assert "partial" in capsys.readouterr().out


def test_partial_host_without_a_success_shows_never(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = _destination(tmp_path)
    partial = {**_record("dirac", NOW, result_status="partial"), "last_success": None}
    _write(dest, "dirac", json.dumps(partial))
    assert report(dest, now=NOW) == 1
    assert "never" in capsys.readouterr().out


def test_ok_host_without_a_success_time_is_stale(tmp_path: Path) -> None:
    dest = _destination(tmp_path)
    ok_without_success = {**_record("dirac", NOW), "last_success": None}
    _write(dest, "dirac", json.dumps(ok_without_success))
    assert report(dest, now=NOW) == 1


@pytest.mark.parametrize("content", MALFORMED)
def test_malformed_record_is_unreadable_and_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str
) -> None:
    dest = _destination(tmp_path)
    _write(dest, "dirac", content)
    assert report(dest, now=NOW) == 1
    assert "UNREADABLE" in capsys.readouterr().out


def test_missing_sentinel_exits_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dest = tmp_path / "nas"
    dest.mkdir()
    assert report(dest, now=NOW) == 2
    assert "sentinel missing" in capsys.readouterr().out


def test_destination_without_records_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = _destination(tmp_path)
    assert report(dest, now=NOW) == 1
    assert "no host has a last-run record" in capsys.readouterr().out


def test_parse_duration_accepts_units_and_rejects_a_bare_number() -> None:
    assert parse_duration("48h") == timedelta(hours=48)
    assert parse_duration("90s") == timedelta(seconds=90)
    assert parse_duration("2d") == timedelta(days=2)
    with pytest.raises(ValueError, match="expected a duration"):
        parse_duration("48")


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dest: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(f'host = "dirac"\ndest_root = "{dest}"\n', encoding="utf-8")
    monkeypatch.setenv("AGENT_SESSION_BACKUP_CONFIG", str(config))


def test_status_command_applies_max_age(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dest = _destination(tmp_path)
    _configure(tmp_path, monkeypatch, dest)
    _write(dest, "dirac", json.dumps(_record("dirac", datetime.now(UTC) - timedelta(hours=49))))
    assert main(["status"]) == 1
    assert main(["status", "--max-age", "72h"]) == 0


def test_status_command_rejects_a_malformed_max_age(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = _destination(tmp_path)
    _configure(tmp_path, monkeypatch, dest)
    with pytest.raises(SystemExit) as info:
        main(["status", "--max-age", "3x"])
    assert info.value.code == 2


def test_status_command_exits_two_without_the_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "nas"
    dest.mkdir()
    _configure(tmp_path, monkeypatch, dest)
    assert main(["status"]) == 2


def test_status_command_reports_configuration_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    config.write_text('host = "My_Mac"\n', encoding="utf-8")
    monkeypatch.setenv("AGENT_SESSION_BACKUP_CONFIG", str(config))
    assert main(["status"]) == 2
    assert "host" in capsys.readouterr().err
