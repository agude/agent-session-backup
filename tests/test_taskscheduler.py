"""Tests for the Windows Task Scheduler backend. A recording runner stands in for schtasks."""

import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from agent_session_backup import cli, schedule
from agent_session_backup.schedule import taskscheduler
from agent_session_backup.schedule.commands import CommandResult
from agent_session_backup.schedule.taskscheduler import TASK_NAME, TaskSchedulerError

EXECUTABLE = r"C:\Users\tester\.local\bin\agent-session-backup.exe"
USER = r"MAXWELL\tester"


class FakeSchtasks:
    """Records schtasks calls. The task exists after /Create and is gone after /Delete."""

    def __init__(self, *, exists: bool = False, fail: str | None = None) -> None:
        self.exists = exists
        self.fail = fail
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str], stdin: str | None) -> CommandResult:
        call = list(args)
        self.calls.append(call)
        action = call[1]
        if action == self.fail:
            return CommandResult(1, "", "ERROR: Access is denied.")
        if action == "/Query":
            return CommandResult(0 if self.exists else 1, "", "")
        if action == "/Create":
            self.exists = True
        elif action == "/Delete":
            self.exists = False
        return CommandResult(0, "SUCCESS", "")

    def actions(self) -> list[str]:
        return [call[1] for call in self.calls]


def test_build_xml_is_utf16_and_runs_the_executable_hidden_while_signed_in() -> None:
    text = taskscheduler.build_xml(EXECUTABLE, USER).decode("utf-16")
    assert text.startswith('<?xml version="1.0" encoding="UTF-16"?>')
    assert f"<UserId>{USER}</UserId>" in text
    assert "<LogonType>InteractiveToken</LogonType>" in text
    assert "<RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>" in text
    assert "<StartWhenAvailable>true</StartWhenAvailable>" in text
    assert "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>" in text
    assert "<Interval>PT1H</Interval>" in text
    assert f"<Arguments>--headless &quot;{EXECUTABLE}&quot; run</Arguments>" not in text
    assert f'<Arguments>--headless "{EXECUTABLE}" run</Arguments>' in text
    assert "\r\n" in text


def test_build_xml_escapes_markup_in_the_user_and_path() -> None:
    text = taskscheduler.build_xml(r"C:\a&b\x.exe", "D<1>\\u").decode("utf-16")
    assert "<UserId>D&lt;1&gt;\\u</UserId>" in text
    assert r"C:\a&amp;b\x.exe" in text


def test_install_registers_the_stored_definition_then_reports_unchanged() -> None:
    fake = FakeSchtasks()
    assert taskscheduler.install(EXECUTABLE, fake, USER) == "installed"
    stored = taskscheduler.xml_path()
    assert stored.read_bytes() == taskscheduler.build_xml(EXECUTABLE, USER)
    assert fake.calls[-1] == ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(stored), "/F"]
    assert taskscheduler.install(EXECUTABLE, fake, USER) == "unchanged"
    assert fake.actions() == ["/Query", "/Create", "/Query"]


def test_a_changed_executable_or_a_missing_task_registers_again() -> None:
    fake = FakeSchtasks()
    taskscheduler.install(EXECUTABLE, fake, USER)
    assert taskscheduler.install(r"C:\other\agent-session-backup.exe", fake, USER) == "updated"
    fake.exists = False
    assert taskscheduler.install(r"C:\other\agent-session-backup.exe", fake, USER) == "installed"


def test_dry_run_install_writes_nothing_and_only_queries() -> None:
    fake = FakeSchtasks()
    assert taskscheduler.install(EXECUTABLE, fake, USER, dry_run=True) == "installed"
    assert not taskscheduler.xml_path().exists()
    fake.exists = True
    assert taskscheduler.install(EXECUTABLE, fake, USER, dry_run=True) == "updated"
    assert fake.actions() == ["/Query", "/Query"]


def test_uninstall_deletes_the_task_and_its_definition_then_reports_absent() -> None:
    fake = FakeSchtasks()
    taskscheduler.install(EXECUTABLE, fake, USER)
    assert taskscheduler.uninstall(fake, dry_run=True) == "removed"
    assert taskscheduler.uninstall(fake) == "removed"
    assert not taskscheduler.xml_path().exists()
    assert taskscheduler.uninstall(fake) == "absent"
    assert "/Delete" in fake.actions()


def test_refused_requests_and_a_missing_schtasks_raise() -> None:
    with pytest.raises(TaskSchedulerError, match="/Create failed: ERROR: Access is denied"):
        taskscheduler.install(EXECUTABLE, FakeSchtasks(fail="/Create"), USER)
    with pytest.raises(TaskSchedulerError, match="/Delete failed"):
        taskscheduler.uninstall(FakeSchtasks(exists=True, fail="/Delete"))

    def missing(args: Sequence[str], stdin: str | None) -> CommandResult:
        return CommandResult(127, "", "[WinError 2] The system cannot find the file specified")

    with pytest.raises(TaskSchedulerError, match="schtasks cannot run"):
        taskscheduler.install(EXECUTABLE, missing, USER)


def test_current_user_reads_the_domain_and_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USERNAME", "tester")
    monkeypatch.setenv("USERDOMAIN", "MAXWELL")
    assert taskscheduler.current_user() == r"MAXWELL\tester"
    monkeypatch.delenv("USERDOMAIN")
    assert taskscheduler.current_user() == "tester"
    monkeypatch.delenv("USERNAME")
    with pytest.raises(TaskSchedulerError, match="USERNAME"):
        taskscheduler.current_user()


def test_windows_dispatches_to_task_scheduler(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, object]] = []

    def fake_install(executable: str, *, dry_run: bool = False) -> str:
        calls.append(("install", (executable, dry_run)))
        return "installed"

    def fake_uninstall(*, dry_run: bool = False) -> str:
        calls.append(("uninstall", dry_run))
        return "absent"

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(taskscheduler, "install", fake_install)
    monkeypatch.setattr(taskscheduler, "uninstall", fake_uninstall)
    assert schedule.install(EXECUTABLE, dry_run=True) == "installed"
    assert schedule.uninstall() == "absent"
    assert calls == [("install", (EXECUTABLE, True)), ("uninstall", False)]


def test_default_executable_adds_exe_for_a_windows_launcher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launcher = tmp_path / "bin" / "agent-session-backup"
    launcher.parent.mkdir()
    monkeypatch.setattr(sys, "argv", [str(launcher)])
    monkeypatch.setattr(sys, "platform", "win32")
    assert cli._default_executable() == str(launcher)
    launcher.with_name("agent-session-backup.exe").write_bytes(b"")
    assert cli._default_executable() == f"{launcher}.exe"
    monkeypatch.setattr(sys, "platform", "linux")
    assert cli._default_executable() == str(launcher)
