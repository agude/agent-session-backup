"""Tests for configuration loading."""

import socket
import sys
from pathlib import Path

import pytest

from agent_session_backup.config import Config, ConfigError, Sources, load


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate HOME, the hostname, the platform, and the environment variables."""
    home_dir = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("AGENT_SESSION_BACKUP_CONFIG", raising=False)
    monkeypatch.setattr(socket, "gethostname", lambda: "Dirac.local")
    monkeypatch.setattr(sys, "platform", "linux")
    return home_dir


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_file_on_linux_yields_defaults(tmp_path: Path, home: Path) -> None:
    assert load(tmp_path / "absent.toml") == Config(
        host="dirac",
        dest_root=home / "synology" / "Archive" / "agent-sessions",
        claude_home=home / ".claude",
        codex_home=home / ".codex",
        sources=Sources(claude=True, codex=True),
    )


def test_missing_file_on_macos_requires_dest_root(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(ConfigError, match="dest_root"):
        load(tmp_path / "absent.toml")


def test_macos_accepts_dest_root_from_file(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    path = write_config(tmp_path, 'dest_root = "/Volumes/Archive/agent-sessions"\n')
    assert load(path).dest_root == Path("/Volumes/Archive/agent-sessions")


def test_full_file_overrides_every_default(tmp_path: Path, home: Path) -> None:
    path = write_config(
        tmp_path,
        """
host = "my-mac"
dest_root = "/mnt/archive"
claude_home = "/data/claude"
codex_home = "/data/codex"

[sources]
claude = false
codex = false
""",
    )
    assert load(path) == Config(
        host="my-mac",
        dest_root=Path("/mnt/archive"),
        claude_home=Path("/data/claude"),
        codex_home=Path("/data/codex"),
        sources=Sources(claude=False, codex=False),
    )


def test_tilde_in_path_values_expands_to_home(tmp_path: Path, home: Path) -> None:
    path = write_config(tmp_path, 'dest_root = "~/x"\n')
    assert load(path).dest_root == home / "x"


def test_codex_home_environment_applies_when_file_omits_it(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-env"))
    assert load(tmp_path / "absent.toml").codex_home == tmp_path / "codex-env"


def test_file_codex_home_overrides_environment(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-env"))
    path = write_config(tmp_path, 'codex_home = "/data/codex"\n')
    assert load(path).codex_home == Path("/data/codex")


def test_no_argument_reads_the_override_path(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_config(tmp_path, 'host = "dirac"\ndest_root = "/mnt/x"\n')
    monkeypatch.setenv("AGENT_SESSION_BACKUP_CONFIG", str(path))
    assert load().host == "dirac"


def test_invalid_default_host_names_the_host_key(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: "My_Mac.local")
    with pytest.raises(ConfigError, match="host must match") as info:
        load(tmp_path / "absent.toml")
    assert "my_mac" in str(info.value)


def test_invalid_toml_names_the_file(tmp_path: Path, home: Path) -> None:
    path = write_config(tmp_path, "host = \n")
    with pytest.raises(ConfigError, match="invalid TOML") as info:
        load(path)
    assert str(path) in str(info.value)


def test_unreadable_file_names_the_file(tmp_path: Path, home: Path) -> None:
    directory = tmp_path / "config-dir"
    directory.mkdir()
    with pytest.raises(ConfigError, match="cannot read the file") as info:
        load(directory)
    assert str(directory) in str(info.value)


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ('dest_rot = "/mnt"\n', "dest_rot"),
        ("[sources]\nfoo = true\n", "sources.foo"),
        ('host = "My_Mac"\n', "host"),
        ("host = 5\n", "host"),
        ("dest_root = 5\n", "dest_root"),
        ("claude_home = 1\n", "claude_home"),
        ("codex_home = []\n", "codex_home"),
        ("sources = true\n", "sources"),
        ('[sources]\nclaude = "yes"\n', "sources.claude"),
    ],
)
def test_invalid_values_name_the_file_and_key(
    tmp_path: Path, home: Path, text: str, key: str
) -> None:
    path = write_config(tmp_path, text)
    with pytest.raises(ConfigError) as info:
        load(path)
    assert str(path) in str(info.value)
    assert key in str(info.value)
