"""Tests for the init command."""

from pathlib import Path

import pytest

from agent_session_backup import destination
from agent_session_backup.cli import main


def write_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    config = tmp_path / "config.toml"
    config.write_text(text, encoding="utf-8")
    monkeypatch.setenv("AGENT_SESSION_BACKUP_CONFIG", str(config))


def test_init_refuses_a_local_destination_and_creates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = tmp_path / "archive"
    write_config(tmp_path, monkeypatch, f'host = "dirac"\ndest_root = "{archive}"\n')
    monkeypatch.setattr(destination, "filesystem_type", lambda path: "ext4")
    assert main(["init"]) == 1
    assert not archive.exists()
    assert "not a network share" in capsys.readouterr().err


def test_init_with_allow_local_writes_one_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "archive"
    write_config(tmp_path, monkeypatch, f'host = "dirac"\ndest_root = "{archive}"\n')
    assert main(["init", "--allow-local"]) == 0
    sentinel = archive / destination.SENTINEL_NAME
    first = sentinel.read_bytes()
    assert main(["init", "--allow-local"]) == 0
    assert sentinel.read_bytes() == first


def test_init_reports_configuration_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(tmp_path, monkeypatch, 'host = "My_Mac"\n')
    assert main(["init"]) == 2
    assert "host" in capsys.readouterr().err


def test_init_reports_unexpected_os_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = tmp_path / "archive"
    write_config(tmp_path, monkeypatch, f'host = "dirac"\ndest_root = "{archive}"\n')

    def fail(dest_root: Path, host: str, *, allow_local: bool = False) -> bool:
        raise OSError("disk full")

    monkeypatch.setattr(destination, "init", fail)
    assert main(["init"]) == 1
    assert "disk full" in capsys.readouterr().err
