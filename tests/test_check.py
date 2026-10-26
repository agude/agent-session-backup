"""Tests for the check command."""

import os
from pathlib import Path

import pytest

from agent_session_backup import destination, paths
from agent_session_backup.cli import main

HOST = "dirac"
ORIGINAL = b'{"n": 1}\n' * 3


def _homes(tmp_path: Path) -> tuple[Path, Path]:
    claude = tmp_path / "claude"
    for name in ("projects/a/s1.jsonl", "history.jsonl"):
        path = claude / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(ORIGINAL)
    archive = tmp_path / "nas"
    archive.mkdir()
    (archive / destination.SENTINEL_NAME).write_text("{}\n", encoding="utf-8")
    return claude, archive


def _configure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, claude: Path, archive: Path
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        f'host = "{HOST}"\n'
        f'dest_root = "{archive}"\n'
        f'claude_home = "{claude}"\n'
        f'codex_home = "{tmp_path / "absent"}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_SESSION_BACKUP_CONFIG", str(config))


def _tree(root: Path) -> list[tuple[str, int, int]]:
    entries = []
    for path in root.rglob("*"):
        status = path.stat()
        entries.append((path.relative_to(root).as_posix(), status.st_size, status.st_mtime_ns))
    return sorted(entries)


def test_check_reports_the_copies_and_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    claude, archive = _homes(tmp_path)
    _configure(tmp_path, monkeypatch, claude, archive)
    before = _tree(archive)
    assert main(["check"]) == 0
    assert "copy: 2" in capsys.readouterr().out
    assert _tree(archive) == before


def test_check_with_no_sentinel_exits_three_and_prints_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    claude, archive = _homes(tmp_path)
    (archive / destination.SENTINEL_NAME).unlink()
    _configure(tmp_path, monkeypatch, claude, archive)
    assert main(["check"]) == 3
    assert "unavailable: sentinel missing" in capsys.readouterr().out


def test_check_creates_no_state_directory_or_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude, archive = _homes(tmp_path)
    _configure(tmp_path, monkeypatch, claude, archive)
    assert not paths.state_dir().exists()
    assert main(["check"]) == 0
    assert not paths.state_dir().exists()


def test_verbose_check_lists_each_planned_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    claude, archive = _homes(tmp_path)
    _configure(tmp_path, monkeypatch, claude, archive)
    assert main(["check", "-v"]) == 0
    output = capsys.readouterr().out
    assert "copy claude/history.jsonl" in output
    assert "copy claude/projects/a/s1.jsonl" in output


def test_undefined_copy_case_is_listed_as_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    claude, archive = _homes(tmp_path)
    _configure(tmp_path, monkeypatch, claude, archive)
    copy = archive / HOST / "claude" / "history.jsonl"
    copy.parent.mkdir(parents=True)
    copy.write_bytes(ORIGINAL)
    stamp = (claude / "history.jsonl").stat().st_mtime
    os.utime(copy, (stamp + 10, stamp + 10))
    assert main(["check"]) == 0
    output = capsys.readouterr().out
    assert "errors: 1" in output
    assert "no action" in output


def test_check_reports_configuration_errors_and_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    config.write_text('host = "My_Mac"\n', encoding="utf-8")
    monkeypatch.setenv("AGENT_SESSION_BACKUP_CONFIG", str(config))
    assert main(["check"]) == 2
    assert "host" in capsys.readouterr().err


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any directory")
def test_unreadable_directory_is_listed_as_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    claude, archive = _homes(tmp_path)
    _configure(tmp_path, monkeypatch, claude, archive)
    locked = claude / "projects" / "a"
    locked.chmod(0)
    try:
        assert main(["check"]) == 0
    finally:
        locked.chmod(0o700)
    assert "errors: 1" in capsys.readouterr().out
