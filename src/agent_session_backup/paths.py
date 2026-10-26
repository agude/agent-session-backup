"""Locate the configuration file, the state directory, and the log directory."""

import os
import sys
from pathlib import Path

APP_NAME = "agent-session-backup"
CONFIG_FILE_VARIABLE = "AGENT_SESSION_BACKUP_CONFIG"


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _xdg_app_dir(variable: str, default: str) -> Path:
    """Return this application's directory under an XDG base directory.

    The XDG specification ignores a relative value, so only an absolute value
    of ``variable`` is used. Otherwise the base is ``default`` under the home
    directory.
    """
    base = Path(os.environ.get(variable, ""))
    if not base.is_absolute():
        base = Path.home() / default
    return base / APP_NAME


def config_dir() -> Path:
    """Return the directory that holds ``config.toml``."""
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
    """Return the directory for the run lock and the logs.

    macOS keeps logs in ``~/Library/Logs`` and ignores ``XDG_STATE_HOME``.
    """
    if _is_macos():
        return Path.home() / "Library" / "Logs" / APP_NAME
    return _xdg_app_dir("XDG_STATE_HOME", ".local/state")
