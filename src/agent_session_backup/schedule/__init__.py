"""Install and remove the hourly backup job with the scheduler for this platform."""

import os
import sys
import tempfile
from pathlib import Path

from agent_session_backup.schedule import cron, launchd
from agent_session_backup.schedule.commands import ScheduleError


class UnsupportedPlatform(ScheduleError):
    """No scheduler backend exists for this platform."""


def install(executable: str) -> str:
    """Install or update the hourly job. Return ``installed``, ``updated``, or ``unchanged``."""
    if sys.platform.startswith("linux"):
        return cron.install(executable)
    if sys.platform.startswith("darwin"):
        return launchd.install(executable)
    raise UnsupportedPlatform(f"no scheduler backend for platform {sys.platform!r}")


def uninstall() -> str:
    """Remove the hourly job. Return ``removed``, or ``absent`` if there is no job."""
    if sys.platform.startswith("linux"):
        return cron.uninstall()
    if sys.platform.startswith("darwin"):
        return launchd.uninstall()
    raise UnsupportedPlatform(f"no scheduler backend for platform {sys.platform!r}")


def validate_executable(executable: str) -> str | None:
    """Return why ``executable`` cannot be scheduled, or ``None`` if it can be."""
    path = Path(executable)
    if not path.is_absolute():
        return f"the executable must be an absolute path: {executable}"
    if not path.is_file() or not os.access(path, os.X_OK):
        return f"the executable is not an executable file: {executable}"
    temporary = Path(tempfile.gettempdir()).resolve()
    if path.resolve().is_relative_to(temporary):
        return f"the executable is under the temporary directory: {executable}"
    return None
