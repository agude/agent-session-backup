"""Tests for the install-schedule and uninstall-schedule commands."""

import sys
import tempfile
from pathlib import Path

import pytest

from agent_session_backup.cli import main
from agent_session_backup.schedule import cron
from tests.conftest import FakeCrontab


@pytest.fixture(autouse=True)
def linux_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise cron CLI cases even when the suite runs on macOS."""
    monkeypatch.setattr(sys, "platform", "linux")


def _write_program(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def program(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An executable in the isolated home's ~/.local/bin, outside any virtual environment.

    The temporary directory is moved away from tmp_path, so tmp_path is not refused.
    """
    path = tmp_path / "isolated-home" / ".local" / "bin" / "agent-session-backup"
    _write_program(path)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "fake-tmp"))
    return path


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


def test_executable_inside_a_virtual_environment_is_refused(
    fake_crontab: FakeCrontab,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    venv = tmp_path / "venv"
    program = venv / "bin" / "agent-session-backup"
    _write_program(program)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "fake-tmp"))
    assert main(["install-schedule", "--executable", str(program)]) == 2
    assert "virtual environment" in capsys.readouterr().err
    assert not fake_crontab.state.exists()


def test_symlink_to_a_program_in_a_virtual_environment_is_accepted(
    fake_crontab: FakeCrontab,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tool = tmp_path / "tools" / "venv"
    target = tool / "bin" / "agent-session-backup"
    _write_program(target)
    (tool / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    link = tmp_path / "isolated-home" / ".local" / "bin" / "agent-session-backup"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "fake-tmp"))
    assert main(["install-schedule", "--executable", str(link)]) == 0
    assert capsys.readouterr().out == "installed\n"
    assert f"@hourly {link} run" in fake_crontab.text()
    assert str(target) not in fake_crontab.text()


def test_default_executable_is_the_normalized_absolute_path_of_argv0(
    fake_crontab: FakeCrontab,
    program: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    relative = "../isolated-home/.local/bin/agent-session-backup"
    monkeypatch.setattr(sys, "argv", [relative, *sys.argv[1:]])
    assert main(["install-schedule"]) == 0
    assert f"@hourly {program} run" in fake_crontab.text()
    assert ".." not in fake_crontab.text()


def test_install_prints_one_word_and_a_repeat_is_unchanged(
    fake_crontab: FakeCrontab, program: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["install-schedule", "--executable", str(program)]) == 0
    assert capsys.readouterr().out == "installed\n"
    writes = fake_crontab.writes()
    assert main(["install-schedule", "--executable", str(program)]) == 0
    assert capsys.readouterr().out == "unchanged\n"
    assert fake_crontab.writes() == writes


def test_two_blocks_make_install_exit_nonzero_and_leave_the_crontab_alone(
    fake_crontab: FakeCrontab, program: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    block = "\n".join((cron.BEGIN, "@hourly /x run", cron.END)) + "\n"
    fake_crontab.write(block + block)
    before = fake_crontab.text()
    assert main(["install-schedule", "--executable", str(program)]) == 1
    assert fake_crontab.text() == before
    assert "more than one" in capsys.readouterr().err


def test_uninstall_prints_removed_then_absent(
    fake_crontab: FakeCrontab, program: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["install-schedule", "--executable", str(program)]) == 0
    capsys.readouterr()
    assert main(["uninstall-schedule"]) == 0
    assert capsys.readouterr().out == "removed\n"
    assert main(["uninstall-schedule"]) == 0
    assert capsys.readouterr().out == "absent\n"


def test_unsupported_platform_exits_one(
    fake_crontab: FakeCrontab,
    program: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "platform", "freebsd13")
    assert main(["install-schedule", "--executable", str(program)]) == 1
    assert "no scheduler backend" in capsys.readouterr().err
    assert main(["uninstall-schedule"]) == 1


def test_dry_run_flags_print_the_result_and_leave_the_crontab_alone(
    fake_crontab: FakeCrontab, program: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["install-schedule", "--dry-run", "--executable", str(program)]) == 0
    assert capsys.readouterr().out == "installed\n"
    assert not fake_crontab.state.exists()
    assert main(["uninstall-schedule", "--dry-run"]) == 0
    assert capsys.readouterr().out == "absent\n"
    assert main(["install-schedule", "--executable", str(program)]) == 0
    capsys.readouterr()
    before = fake_crontab.text()
    assert main(["install-schedule", "--dry-run", "--executable", str(program)]) == 0
    assert capsys.readouterr().out == "unchanged\n"
    assert main(["uninstall-schedule", "--dry-run"]) == 0
    assert capsys.readouterr().out == "removed\n"
    assert fake_crontab.text() == before
