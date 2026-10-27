"""Install and remove the hourly backup job with the scheduler for this platform."""

import os
import sys
import tempfile
from pathlib import Path

from agent_session_backup.schedule import cron, launchd
from agent_session_backup.schedule.commands import ScheduleError


class UnsupportedPlatform(ScheduleError):
    """No scheduler backend exists for this platform."""


def install(executable: str, *, dry_run: bool = False) -> str:
    """Install or update the hourly job. Return ``installed``, ``updated``, or ``unchanged``.

    With ``dry_run``, return the result that an install would have and change nothing.
    """
    if sys.platform.startswith("linux"):
        return cron.install(executable, dry_run=dry_run)
    if sys.platform.startswith("darwin"):
        return launchd.install(executable, dry_run=dry_run)
    raise UnsupportedPlatform(f"no scheduler backend for platform {sys.platform!r}")


def uninstall(*, dry_run: bool = False) -> str:
    """Remove the hourly job. Return ``removed``, or ``absent`` if there is no job.

    With ``dry_run``, return the result that an uninstall would have and change nothing.
    """
    if sys.platform.startswith("linux"):
        return cron.uninstall(dry_run=dry_run)
    if sys.platform.startswith("darwin"):
        return launchd.uninstall(dry_run=dry_run)
    raise UnsupportedPlatform(f"no scheduler backend for platform {sys.platform!r}")


def validate_executable(executable: str) -> str | None:
    """Return why ``executable`` cannot be scheduled, or ``None`` if it can be."""
    path = Path(executable)
    if not path.is_absolute():
        return f"the executable must be an absolute path: {executable}"
    if not path.is_file() or not os.access(path, os.X_OK):
        return f"the executable is not an executable file: {executable}"
    if (path.parent.parent / "pyvenv.cfg").is_file():
        return f"the executable is inside a virtual environment: {executable}"
    temporary = Path(tempfile.gettempdir()).resolve()
    if path.resolve().is_relative_to(temporary):
        return f"the executable is under the temporary directory: {executable}"
    return None
