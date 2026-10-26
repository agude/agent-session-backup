"""Tests for the launchd backend, using a fake launchctl. No test runs the real one."""

import plistlib
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from agent_session_backup import schedule
from agent_session_backup.schedule import launchd
from agent_session_backup.schedule.commands import CommandResult, ScheduleError

EXE = "/Users/agude/.local/bin/agent-session-backup"
UID = 501


class FakeLaunchctl:
    """Records each call and answers with the statuses set on it."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.print_status = 0
        self.bootout_status = 0
        self.bootout_stderr = ""
        self.bootstrap_status = 0

    def __call__(self, args: Sequence[str], stdin: str | None) -> CommandResult:
        self.calls.append(list(args))
        verb = args[1]
        if verb == "print":
            return CommandResult(self.print_status, "", "")
        if verb == "bootout":
            return CommandResult(self.bootout_status, "", self.bootout_stderr)
        return CommandResult(self.bootstrap_status, "", "bootstrap refused")

    def verbs(self) -> list[str]:
        return [call[1] for call in self.calls]


@pytest.fixture
def fake() -> FakeLaunchctl:
    return FakeLaunchctl()


def test_plist_round_trips_with_the_documented_keys(tmp_path: Path) -> None:
    log = tmp_path / "launchd.log"
    assert plistlib.loads(launchd.build_plist(EXE, log)) == {
        "Label": launchd.LABEL,
        "ProgramArguments": [EXE, "run"],
        "StartInterval": 3600,
        "RunAtLoad": False,
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }


def test_first_install_boots_out_then_bootstraps(fake: FakeLaunchctl) -> None:
    assert launchd.install(EXE, runner=fake, uid=UID) == "installed"
    assert fake.verbs() == ["bootout", "bootstrap"]
    assert plistlib.loads(launchd.plist_path().read_bytes())["ProgramArguments"] == [EXE, "run"]


def test_repeat_install_with_identical_plist_is_unchanged_without_bootstrap(
    fake: FakeLaunchctl,
) -> None:
    launchd.install(EXE, runner=fake, uid=UID)
    fake.calls.clear()
    assert launchd.install(EXE, runner=fake, uid=UID) == "unchanged"
    assert fake.verbs() == ["print"]


def test_changed_executable_is_updated_and_the_plist_is_rewritten(fake: FakeLaunchctl) -> None:
    launchd.install(EXE, runner=fake, uid=UID)
    new_exe = "/usr/local/bin/agent-session-backup"
    assert launchd.install(new_exe, runner=fake, uid=UID) == "updated"
    written = plistlib.loads(launchd.plist_path().read_bytes())
    assert written["ProgramArguments"] == [new_exe, "run"]


def test_identical_plist_for_a_job_that_is_not_loaded_is_reloaded(fake: FakeLaunchctl) -> None:
    launchd.install(EXE, runner=fake, uid=UID)
    fake.print_status = 113
    fake.calls.clear()
    assert launchd.install(EXE, runner=fake, uid=UID) == "updated"
    assert fake.verbs() == ["print", "bootout", "bootstrap"]


def test_bootout_of_a_job_that_is_not_loaded_is_ignored(fake: FakeLaunchctl) -> None:
    fake.bootout_status = 3
    fake.bootout_stderr = "Boot-out failed: 3: No such process"
    assert launchd.install(EXE, runner=fake, uid=UID) == "installed"


def test_other_bootout_failures_are_errors(fake: FakeLaunchctl) -> None:
    fake.bootout_status = 5
    fake.bootout_stderr = "Operation not permitted"
    with pytest.raises(launchd.LaunchdError, match="bootout failed"):
        launchd.install(EXE, runner=fake, uid=UID)


def test_bootstrap_failure_is_an_error(fake: FakeLaunchctl) -> None:
    fake.bootstrap_status = 5
    with pytest.raises(launchd.LaunchdError, match="bootstrap refused"):
        launchd.install(EXE, runner=fake, uid=UID)


def test_uninstall_removes_the_plist_then_reports_absent(fake: FakeLaunchctl) -> None:
    launchd.install(EXE, runner=fake, uid=UID)
    assert launchd.uninstall(runner=fake, uid=UID) == "removed"
    assert not launchd.plist_path().exists()
    assert launchd.uninstall(runner=fake, uid=UID) == "absent"


def test_launchd_errors_are_schedule_errors() -> None:
    assert issubclass(launchd.LaunchdError, ScheduleError)


def test_schedule_dispatches_to_launchd_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(launchd, "install", lambda executable: "installed")
    monkeypatch.setattr(launchd, "uninstall", lambda: "absent")
    assert schedule.install(EXE) == "installed"
    assert schedule.uninstall() == "absent"
