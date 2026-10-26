"""Tests for one backup pass."""

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agent_session_backup import destination
from agent_session_backup.config import Config, Sources
from agent_session_backup.lock import run_lock
from agent_session_backup.runner import configure_logging, run_pass

HOST = "dirac"
T0 = datetime(2026, 10, 26, 18, 0, 0, tzinfo=UTC)
STAMP = "2026-10-26T18:00:00Z"
ORIGINAL = b'{"n": 1}\n' * 3
RECORD_KEYS = {
    "host",
    "version",
    "started",
    "finished",
    "status",
    "last_success",
    "counts",
    "bytes_copied",
    "errors",
}
CLAUDE_FILES = (
    "projects/a/s1.jsonl",
    "projects/a/s1/subagents/t.jsonl",
    "projects/a/memory/MEMORY.md",
    "history.jsonl",
)
CODEX_FILES = ("sessions/2026/10/26/rollout-x.jsonl", "session_index.jsonl")
EXPECTED = sorted(
    [f"claude/{name}" for name in CLAUDE_FILES] + [f"codex/{name}" for name in CODEX_FILES]
)


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@dataclass(frozen=True)
class World:
    config: Config
    state: Path
    claude: Path
    dest: Path

    @property
    def host_dir(self) -> Path:
        return self.dest / HOST


@pytest.fixture
def world(tmp_path: Path) -> World:
    claude = tmp_path / "claude"
    codex = tmp_path / "codex"
    for name in CLAUDE_FILES:
        _write(claude / name, ORIGINAL)
    for name in CODEX_FILES:
        _write(codex / name, ORIGINAL)
    dest = tmp_path / "nas"
    _write(dest / destination.SENTINEL_NAME, b"{}\n")
    config = Config(
        host=HOST,
        dest_root=dest,
        claude_home=claude,
        codex_home=codex,
        sources=Sources(),
    )
    return World(config=config, state=tmp_path / "state", claude=claude, dest=dest)


def _run(world: World, *, when: datetime = T0, strict: bool = False, verbose: bool = False) -> int:
    configure_logging(world.state, verbose=verbose)
    return run_pass(world.config, world.state, strict=strict, now=lambda: when)


def _record(world: World) -> Any:
    return json.loads((world.host_dir / ".last-run.json").read_text(encoding="utf-8"))


def _copied(world: World) -> list[str]:
    if not world.host_dir.exists():
        return []
    return sorted(
        path.relative_to(world.host_dir).as_posix()
        for path in world.host_dir.rglob("*")
        if path.is_file() and path.name != ".last-run.json"
    )


def _log(world: World) -> str:
    return (world.state / "backup.log").read_text(encoding="utf-8")


def test_first_run_copies_every_allowlisted_file(world: World) -> None:
    assert _run(world) == 0
    assert _copied(world) == EXPECTED


def test_second_run_copies_nothing_and_skips_everything(world: World) -> None:
    _run(world)
    assert _run(world) == 0
    assert _record(world)["counts"] == {
        "copied": 0,
        "preserved": 0,
        "skipped": len(EXPECTED),
        "errors": 0,
    }


def test_appended_source_is_copied_exactly_once(world: World) -> None:
    _run(world)
    with (world.claude / "history.jsonl").open("ab") as handle:
        handle.write(b'{"n": 2}\n')
    _run(world)
    assert _record(world)["counts"]["copied"] == 1


def test_deleted_source_leaves_the_copy_in_place(world: World) -> None:
    _run(world)
    (world.claude / "history.jsonl").unlink()
    assert _run(world) == 0
    assert (world.host_dir / "claude" / "history.jsonl").read_bytes() == ORIGINAL


def test_truncated_source_keeps_the_old_copy_as_a_backup(world: World) -> None:
    _run(world)
    (world.claude / "history.jsonl").write_bytes(b"x")
    assert _run(world, when=T0 + timedelta(hours=1)) == 0
    assert _record(world)["counts"]["preserved"] == 1
    backup = world.host_dir / "claude" / "history.jsonl.20261026T190000Z.bak"
    assert backup.read_bytes() == ORIGINAL
    assert (world.host_dir / "claude" / "history.jsonl").read_bytes() == b"x"


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any file")
def test_unreadable_source_makes_the_run_partial(world: World) -> None:
    locked = world.claude / "history.jsonl"
    locked.chmod(0)
    try:
        status = _run(world)
    finally:
        locked.chmod(0o600)
    assert status == 1
    record = _record(world)
    assert record["status"] == "partial"
    assert [entry["path"] for entry in record["errors"]] == [str(locked)]
    assert _copied(world) == [name for name in EXPECTED if name != "claude/history.jsonl"]


def test_missing_sentinel_exits_zero_writes_nothing_and_logs_the_reason(world: World) -> None:
    (world.dest / destination.SENTINEL_NAME).unlink()
    assert _run(world) == 0
    assert list(world.dest.iterdir()) == []
    assert "destination unavailable: sentinel missing" in _log(world)


def test_missing_sentinel_exits_three_with_strict(world: World) -> None:
    (world.dest / destination.SENTINEL_NAME).unlink()
    assert _run(world, strict=True) == 3


def test_held_lock_exits_zero_and_copies_nothing(world: World) -> None:
    configure_logging(world.state, verbose=False)
    with run_lock(world.state):
        assert run_pass(world.config, world.state, now=lambda: T0) == 0
    assert _copied(world) == []


def test_successful_run_writes_nothing_to_stderr(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(world) == 0
    assert capsys.readouterr().err == ""


def test_last_run_record_has_the_documented_keys(world: World) -> None:
    _run(world)
    record = _record(world)
    assert set(record) == RECORD_KEYS
    assert set(record["counts"]) == {"copied", "preserved", "skipped", "errors"}
    assert record["host"] == HOST
    assert record["status"] == "ok"
    assert record["started"] == record["finished"] == record["last_success"] == STAMP
    assert record["errors"] == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any file")
def test_last_success_survives_a_partial_run(world: World) -> None:
    _run(world)
    locked = world.claude / "projects" / "a" / "memory"
    locked.chmod(0)
    try:
        assert _run(world, when=T0 + timedelta(hours=2)) == 1
    finally:
        locked.chmod(0o700)
    record = _record(world)
    assert record["status"] == "partial"
    assert record["finished"] == "2026-10-26T20:00:00Z"
    assert record["last_success"] == STAMP


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any file")
@pytest.mark.parametrize("previous", ["not json", "[]"])
def test_partial_run_without_a_usable_previous_record_has_null_last_success(
    world: World, previous: str
) -> None:
    _write(world.host_dir / ".last-run.json", previous.encode("utf-8"))
    locked = world.claude / "history.jsonl"
    locked.chmod(0)
    try:
        assert _run(world) == 1
    finally:
        locked.chmod(0o600)
    assert _record(world)["last_success"] is None


def test_undefined_copy_case_is_reported_and_leaves_the_destination_alone(world: World) -> None:
    _run(world)
    source = world.claude / "history.jsonl"
    target = world.host_dir / "claude" / "history.jsonl"
    stamp = source.stat().st_mtime
    os.utime(target, (stamp + 10, stamp + 10))
    assert _run(world, when=T0 + timedelta(hours=1)) == 1
    assert any("no action" in entry["error"] for entry in _record(world)["errors"])
    assert target.read_bytes() == ORIGINAL


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any directory")
def test_unreadable_directory_makes_the_run_partial(world: World) -> None:
    locked = world.claude / "projects" / "a" / "memory"
    locked.chmod(0)
    try:
        status = _run(world)
    finally:
        locked.chmod(0o700)
    assert status == 1
    assert [entry["path"] for entry in _record(world)["errors"]] == [str(locked)]


def test_run_summary_is_logged_at_info(world: World) -> None:
    _run(world)
    assert f"run ok: copied={len(EXPECTED)} preserved=0 skipped=0 errors=0" in _log(world)


def test_verbose_logs_one_debug_line_per_copy(world: World) -> None:
    _run(world, verbose=True)
    assert _log(world).count(" DEBUG copy ") == len(EXPECTED)


def test_without_verbose_the_log_has_no_debug_lines(world: World) -> None:
    _run(world)
    assert "DEBUG" not in _log(world)
