"""Locate the configuration file, the state directory, and the log directory."""

import os
import sys
from pathlib import Path

APP_NAME = "agent-session-backup"
CONFIG_FILE_VARIABLE = "AGENT_SESSION_BACKUP_CONFIG"


def is_macos() -> bool:
    """Return whether the running platform is macOS."""
    return sys.platform == "darwin"


def is_windows() -> bool:
    """Return whether the running platform is Windows."""
    return sys.platform == "win32"


def _absolute_variable(variable: str) -> Path | None:
    """Return ``variable`` as a path when it is set to an absolute value, else ``None``."""
    base = Path(os.environ.get(variable, ""))
    return base if base.is_absolute() else None


def _xdg_app_dir(variable: str, default: str) -> Path:
    """Return this application's directory under an XDG base directory.

    The XDG specification ignores a relative value, so only an absolute value
    of ``variable`` is used. Otherwise the base is ``default`` under the home
    directory.
    """
    base = _absolute_variable(variable)
    return (Path.home() / default if base is None else base) / APP_NAME


def local_app_data() -> Path:
    """Return the Windows per-user local application data directory."""
    base = _absolute_variable("LOCALAPPDATA")
    return Path.home() / "AppData" / "Local" if base is None else base


def config_dir() -> Path:
    """Return the directory that holds ``config.toml``.

    Windows uses ``%LOCALAPPDATA%`` unless ``XDG_CONFIG_HOME`` is set.
    """
    if is_windows() and _absolute_variable("XDG_CONFIG_HOME") is None:
        return local_app_data() / APP_NAME
    return _xdg_app_dir("XDG_CONFIG_HOME", ".config")


def config_file() -> Path:
    """Return the configuration file path.

    ``AGENT_SESSION_BACKUP_CONFIG`` replaces the default ``config.toml``.
    """
    override = os.environ.get(CONFIG_FILE_VARIABLE, "")
    if override:
        return Path(override).expanduser()
    return config_dir() / "config.toml"


def state_dir() -> Path:
    """Return the directory for the run lock, the logs, and database snapshots.

    macOS keeps logs in ``~/Library/Logs`` and ignores ``XDG_STATE_HOME``.
    Windows uses ``%LOCALAPPDATA%`` unless ``XDG_STATE_HOME`` is set.
    """
    if is_macos():
        return Path.home() / "Library" / "Logs" / APP_NAME
    if is_windows() and _absolute_variable("XDG_STATE_HOME") is None:
        return local_app_data() / APP_NAME
    return _xdg_app_dir("XDG_STATE_HOME", ".local/state")
