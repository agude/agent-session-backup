"""Manage the hourly LaunchAgent for agent-session-backup on macOS."""

import os
import plistlib
from pathlib import Path

from agent_session_backup import paths
from agent_session_backup.schedule.commands import (
    CommandResult,
    Runner,
    ScheduleError,
    run_command,
)

LABEL = "io.github.agude.agent-session-backup"
INTERVAL_SECONDS = 3600
LOG_NAME = "launchd.log"
_NOT_LOADED_MARKERS = ("not loaded", "No such process", "Could not find service")


class LaunchdError(ScheduleError):
    """launchctl refused a request."""


def plist_path() -> Path:
    """Return the path of the user's LaunchAgent property list."""
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def build_plist(executable: str, log_path: Path) -> bytes:
    """Return the property list. launchd requires ``Label``, so it is included."""
    return plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": [executable, "run"],
            "StartInterval": INTERVAL_SECONDS,
            "RunAtLoad": False,
            "StandardOutPath": str(log_path),
            "StandardErrorPath": str(log_path),
        },
        fmt=plistlib.FMT_XML,
    )


def install(executable: str, runner: Runner = run_command, uid: int | None = None) -> str:
    """Install or update the LaunchAgent. Return ``installed``, ``updated``, or ``unchanged``.

    The job is reloaded unless the plist is identical and launchd reports the job
    as loaded.
    """
    domain = f"gui/{os.getuid() if uid is None else uid}"
    target = f"{domain}/{LABEL}"
    plist = plist_path()
    content = build_plist(executable, paths.state_dir() / LOG_NAME)
    existed = plist.exists()
    if (
        existed
        and plist.read_bytes() == content
        and runner(["launchctl", "print", target], None).returncode == 0
    ):
        return "unchanged"
    plist.parent.mkdir(parents=True, exist_ok=True)
    paths.state_dir().mkdir(parents=True, exist_ok=True)
    plist.write_bytes(content)
    _bootout(runner, target)
    _check(runner(["launchctl", "bootstrap", domain, str(plist)], None), "bootstrap")
    return "updated" if existed else "installed"


def uninstall(runner: Runner = run_command, uid: int | None = None) -> str:
    """Stop the job and remove its plist. Return ``removed``, or ``absent`` if there was none."""
    domain = f"gui/{os.getuid() if uid is None else uid}"
    _bootout(runner, f"{domain}/{LABEL}")
    plist = plist_path()
    if not plist.exists():
        return "absent"
    plist.unlink()
    return "removed"


def _bootout(runner: Runner, target: str) -> None:
    result = runner(["launchctl", "bootout", target], None)
    if result.returncode != 0 and not any(
        marker in result.stderr for marker in _NOT_LOADED_MARKERS
    ):
        raise LaunchdError(f"launchctl bootout failed: {result.stderr.strip()}")


def _check(result: CommandResult, action: str) -> None:
    if result.returncode != 0:
        raise LaunchdError(f"launchctl {action} failed: {result.stderr.strip()}")
