"""Manage the hourly Task Scheduler task for agent-session-backup on Windows."""

import os
from pathlib import Path
from xml.sax.saxutils import escape

from agent_session_backup import paths
from agent_session_backup.schedule.commands import (
    MISSING_PROGRAM_STATUS,
    CommandResult,
    Runner,
    ScheduleError,
    run_command,
)

TASK_NAME = "agent-session-backup"
XML_NAME = "task.xml"
# The repetition counts from this moment. A run missed before registration is not owed,
# because the task does not exist yet.
START_BOUNDARY = "2026-01-01T00:00:00"
CONHOST = r"%SystemRoot%\System32\conhost.exe"


class TaskSchedulerError(ScheduleError):
    """schtasks refused a request."""


def xml_path() -> Path:
    """Return where the registered task definition is kept, to detect an unchanged task."""
    return paths.state_dir() / XML_NAME


def current_user() -> str:
    """Return the signed-in user as ``DOMAIN\\name``, the form Task Scheduler stores."""
    name = os.environ.get("USERNAME", "")
    if not name:
        raise TaskSchedulerError("USERNAME is not set")
    domain = os.environ.get("USERDOMAIN", "")
    return f"{domain}\\{name}" if domain else name


def build_xml(executable: str, user: str) -> bytes:
    """Return the task definition, encoded as UTF-16 as schtasks expects.

    The task runs ``executable run`` every hour, only while ``user`` is signed in and
    a network connection is up, so it sees mapped drives. A run missed while the
    machine was off starts when it is next available. conhost --headless keeps a
    console window from opening, and a second run never starts while one is going.
    """
    arguments = f'--headless "{executable}" run'
    text = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Copy Claude Code and Codex sessions to the NAS every hour.</Description>
    <URI>\\{TASK_NAME}</URI>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>{START_BOUNDARY}</StartBoundary>
      <Repetition>
        <Interval>PT1H</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(CONHOST)}</Command>
      <Arguments>{escape(arguments)}</Arguments>
    </Exec>
  </Actions>
</Task>
"""
    return text.replace("\n", "\r\n").encode("utf-16")


def install(
    executable: str,
    runner: Runner = run_command,
    user: str | None = None,
    *,
    dry_run: bool = False,
) -> str:
    """Install or update the task. Return ``installed``, ``updated``, or ``unchanged``.

    The task is registered again unless the stored definition is identical and Task
    Scheduler reports the task. With ``dry_run``, return the same result, write no
    file, and run only ``schtasks /Query``.
    """
    content = build_xml(executable, current_user() if user is None else user)
    stored = xml_path()
    existed = _exists(runner)
    if existed and stored.is_file() and stored.read_bytes() == content:
        return "unchanged"
    if dry_run:
        return "updated" if existed else "installed"
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(content)
    _check(
        runner(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(stored), "/F"], None),
        "/Create",
    )
    return "updated" if existed else "installed"


def uninstall(runner: Runner = run_command, *, dry_run: bool = False) -> str:
    """Delete the task. Return ``removed``, or ``absent`` if there was none.

    With ``dry_run``, return the same result and run only ``schtasks /Query``.
    """
    existed = _exists(runner)
    if dry_run:
        return "removed" if existed else "absent"
    if existed:
        _check(runner(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], None), "/Delete")
    xml_path().unlink(missing_ok=True)
    return "removed" if existed else "absent"


def _exists(runner: Runner) -> bool:
    """Return whether Task Scheduler has the task. schtasks exits 1 for a missing task."""
    result = runner(["schtasks", "/Query", "/TN", TASK_NAME], None)
    if result.returncode == MISSING_PROGRAM_STATUS:
        raise TaskSchedulerError(f"schtasks cannot run: {result.stderr.strip()}")
    return result.returncode == 0


def _check(result: CommandResult, action: str) -> None:
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise TaskSchedulerError(f"schtasks {action} failed: {detail}")
