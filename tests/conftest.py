"""Shared fixtures."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every path the package reads at a temporary directory.

    No test then reads or writes the real home directory, configuration, state,
    or crontab.
    """
    home = tmp_path / "isolated-home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    monkeypatch.setenv("AGENT_SESSION_BACKUP_CONFIG", str(home / "config.toml"))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
