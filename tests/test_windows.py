"""Tests for the Windows paths, configuration defaults, destination check, and desktop sources.

Each test patches sys.platform, so the Windows branches run on every platform.
"""

import socket
import sys
from pathlib import Path, PureWindowsPath

import pytest

from agent_session_backup import destination, paths
from agent_session_backup.config import ConfigError, Sources, load
from agent_session_backup.sources import iter_sources
from tests.conftest import ON_WINDOWS


@pytest.fixture
def windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A Windows-shaped home with no XDG overrides. Return the home directory."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    for name in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "CODEX_HOME", paths.CONFIG_FILE_VARIABLE):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(socket, "gethostname", lambda: "MAXWELL")
    monkeypatch.setattr(sys, "platform", "win32")
    return home


def test_windows_keeps_config_and_state_in_local_app_data(windows: Path) -> None:
    local = windows / "AppData" / "Local" / "agent-session-backup"
    assert paths.config_file() == local / "config.toml"
    assert paths.state_dir() == local


def test_xdg_variables_still_win_on_windows(
    windows: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert paths.config_dir() == tmp_path / "config" / "agent-session-backup"
    assert paths.state_dir() == tmp_path / "state" / "agent-session-backup"


def test_local_app_data_falls_back_under_home(
    windows: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LOCALAPPDATA")
    assert paths.local_app_data() == windows / "AppData" / "Local"


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_windows_requires_dest_root(windows: Path, tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="dest_root is required on Windows"):
        load(tmp_path / "absent.toml")


def test_windows_backs_up_codex_databases_by_default(windows: Path, tmp_path: Path) -> None:
    config = load(write_config(tmp_path, "dest_root = 'Z:/Archive/agent-sessions'\n"))
    assert config.host == "maxwell"
    assert config.sources == Sources(claude_desktop=True, codex_sqlite=True)
    assert config.claude_desktop_home is None


def test_other_platforms_leave_codex_databases_off_unless_asked(
    windows: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert not load(tmp_path / "absent.toml").sources.codex_sqlite
    path = write_config(tmp_path, "[sources]\ncodex_sqlite = true\nclaude_desktop = false\n")
    assert load(path).sources == Sources(claude_desktop=False, codex_sqlite=True)


def test_the_store_app_data_is_found_before_the_direct_install(
    windows: Path, tmp_path: Path
) -> None:
    config_path = write_config(tmp_path, "dest_root = 'Z:/Archive/agent-sessions'\n")
    direct = windows / "AppData" / "Roaming" / "Claude"
    direct.mkdir(parents=True)
    assert load(config_path).claude_desktop_home == direct
    package = windows / "AppData" / "Local" / "Packages" / "AnthropicPBC.Claude_fnn82j28hfe8t"
    store = package / "LocalCache" / "Roaming" / "Claude"
    store.mkdir(parents=True)
    assert load(config_path).claude_desktop_home == store


def test_claude_desktop_home_can_be_set_on_any_platform(
    windows: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    path = write_config(tmp_path, f"claude_desktop_home = '{tmp_path / 'desktop'}'\n")
    assert load(path).claude_desktop_home == tmp_path / "desktop"
    assert load(tmp_path / "absent.toml").claude_desktop_home is None


def test_desktop_session_metadata_is_listed_under_its_own_prefix(
    windows: Path, tmp_path: Path
) -> None:
    desktop = tmp_path / "desktop"
    sessions = desktop / "claude-code-sessions" / "account" / "org"
    sessions.mkdir(parents=True)
    (sessions / "local_1.json").write_text("{}", encoding="utf-8")
    (desktop / "claude-code-sessions" / "scheduled-tasks.json").write_text("[]", encoding="utf-8")
    (desktop / "Cache").mkdir()
    (desktop / "Cache" / "data.json").write_text("{}", encoding="utf-8")
    config = load(
        write_config(
            tmp_path,
            f"dest_root = 'Z:/x'\nclaude_desktop_home = '{desktop}'\n"
            f"claude_home = '{tmp_path / 'none'}'\ncodex_home = '{tmp_path / 'none'}'\n",
        )
    )
    assert [source.relpath.as_posix() for source in iter_sources(config)] == [
        "claude-desktop/claude-code-sessions/account/org/local_1.json",
        "claude-desktop/claude-code-sessions/scheduled-tasks.json",
    ]


@pytest.mark.parametrize(
    ("drive_type", "name"),
    [(2, "removable"), (3, "fixed"), (4, "remote"), (5, "cdrom"), (6, "ramdisk"), (0, "unknown")],
)
def test_a_drive_is_named_by_its_type(
    drive_type: int, name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots: list[str] = []

    def fake(root: str) -> int:
        roots.append(root)
        return drive_type

    monkeypatch.setattr(destination, "_drive_type", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    path = Path("Z:/Archive/agent-sessions") if ON_WINDOWS else Path("/archive")
    assert destination.filesystem_type(path) == name
    assert roots == [path.absolute().anchor]


def test_a_unc_path_is_remote_without_asking_for_a_drive_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(root: str) -> int:
        raise AssertionError("a UNC path has no drive type")

    monkeypatch.setattr(destination, "_drive_type", refuse)
    monkeypatch.setattr(sys, "platform", "win32")
    if not ON_WINDOWS:
        pytest.skip("only a Windows path has a UNC anchor")
    assert (
        destination.filesystem_type(Path(str(PureWindowsPath("//nas/share/archive")))) == "remote"
    )


@pytest.mark.skipif(ON_WINDOWS, reason="Windows has drive types")
def test_drive_types_are_refused_off_windows() -> None:
    with pytest.raises(OSError, match="only on Windows"):
        destination._drive_type("C:\\")
