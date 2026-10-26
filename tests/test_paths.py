"""Tests for the platform directories."""

import sys
from pathlib import Path

import pytest

from agent_session_backup import paths


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point HOME at a directory that does not exist and clear the related variables."""
    home_dir = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home_dir))
    for name in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", paths.CONFIG_FILE_VARIABLE):
        monkeypatch.delenv(name, raising=False)
    return home_dir


def test_config_file_uses_xdg_config_home(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    assert paths.config_file() == xdg / "agent-session-backup" / "config.toml"


def test_config_file_defaults_to_home_config(home: Path) -> None:
    assert paths.config_file() == home / ".config" / "agent-session-backup" / "config.toml"


def test_relative_xdg_value_is_ignored(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/dir")
    assert paths.config_dir() == home / ".config" / "agent-session-backup"


def test_override_replaces_config_file(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom = tmp_path / "custom.toml"
    monkeypatch.setenv(paths.CONFIG_FILE_VARIABLE, str(custom))
    assert paths.config_file() == custom


def test_override_expands_tilde(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(paths.CONFIG_FILE_VARIABLE, "~/custom.toml")
    assert paths.config_file() == home / "custom.toml"


def test_state_dir_on_linux_uses_xdg_state_home(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    xdg = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))
    assert paths.state_dir() == xdg / "agent-session-backup"


def test_state_dir_on_linux_defaults_to_local_state(home: Path) -> None:
    assert paths.state_dir() == home / ".local" / "state" / "agent-session-backup"


def test_state_dir_on_macos_is_under_library_logs(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert paths.state_dir() == home / "Library" / "Logs" / "agent-session-backup"


def test_config_file_on_macos_matches_linux(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    assert paths.config_file() == home / ".config" / "agent-session-backup" / "config.toml"


def test_no_function_creates_a_directory(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    for platform in ("linux", "darwin"):
        monkeypatch.setattr(sys, "platform", platform)
        paths.config_dir()
        paths.config_file()
        paths.state_dir()
    assert list(tmp_path.iterdir()) == []
