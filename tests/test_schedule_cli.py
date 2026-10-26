"""Tests for the install-schedule and uninstall-schedule commands."""

import sys
import tempfile
from pathlib import Path

import pytest

from agent_session_backup.cli import main
from agent_session_backup.schedule import cron
from tests.conftest import FakeCrontab


def test_relative_executable_is_refused(
    fake_crontab: FakeCrontab, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["install-schedule", "--executable", "relative/agent-session-backup"]) == 2
    assert "absolute path" in capsys.readouterr().err
    assert not fake_crontab.state.exists()


def test_executable_under_the_temporary_directory_is_refused(
    fake_crontab: FakeCrontab, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert tmp_path.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
    program = tmp_path / "agent-session-backup"
    program.write_text("#!/bin/sh\n", encoding="utf-8")
    program.chmod(0o755)
    assert main(["install-schedule", "--executable", str(program)]) == 2
    assert "temporary directory" in capsys.readouterr().err
    assert not fake_crontab.state.exists()


def test_non_executable_file_is_refused(
    fake_crontab: FakeCrontab, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    program = tmp_path / "agent-session-backup"
    program.write_text("", encoding="utf-8")
    assert main(["install-schedule", "--executable", str(program)]) == 2
    assert "not an executable file" in capsys.readouterr().err


def test_install_prints_one_word_and_a_repeat_is_unchanged(
    fake_crontab: FakeCrontab, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["install-schedule", "--executable", sys.executable]) == 0
    assert capsys.readouterr().out == "installed\n"
    writes = fake_crontab.writes()
    assert main(["install-schedule", "--executable", sys.executable]) == 0
    assert capsys.readouterr().out == "unchanged\n"
    assert fake_crontab.writes() == writes


def test_two_blocks_make_install_exit_nonzero_and_leave_the_crontab_alone(
    fake_crontab: FakeCrontab, capsys: pytest.CaptureFixture[str]
) -> None:
    block = "\n".join((cron.BEGIN, "@hourly /x run", cron.END)) + "\n"
    fake_crontab.write(block + block)
    before = fake_crontab.text()
    assert main(["install-schedule", "--executable", sys.executable]) == 1
    assert fake_crontab.text() == before
    assert "more than one" in capsys.readouterr().err


def test_uninstall_prints_removed_then_absent(
    fake_crontab: FakeCrontab, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["install-schedule", "--executable", sys.executable]) == 0
    capsys.readouterr()
    assert main(["uninstall-schedule"]) == 0
    assert capsys.readouterr().out == "removed\n"
    assert main(["uninstall-schedule"]) == 0
    assert capsys.readouterr().out == "absent\n"


def test_unsupported_platform_exits_one(
    fake_crontab: FakeCrontab, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "platform", "freebsd13")
    assert main(["install-schedule", "--executable", sys.executable]) == 1
    assert "no scheduler backend" in capsys.readouterr().err
    assert main(["uninstall-schedule"]) == 1
