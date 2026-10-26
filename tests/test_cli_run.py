"""Tests for the run command."""

from pathlib import Path

import pytest

from agent_session_backup import destination
from agent_session_backup.cli import main


def write_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    config = tmp_path / "config.toml"
    config.write_text(text, encoding="utf-8")
    monkeypatch.setenv("AGENT_SESSION_BACKUP_CONFIG", str(config))


def test_run_copies_from_the_configured_homes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude = tmp_path / "claude"
    (claude / "history.jsonl").parent.mkdir(parents=True)
    (claude / "history.jsonl").write_bytes(b"{}\n")
    archive = tmp_path / "nas"
    archive.mkdir()
    (archive / destination.SENTINEL_NAME).write_text("{}\n", encoding="utf-8")
    write_config(
        tmp_path,
        monkeypatch,
        f'host = "dirac"\ndest_root = "{archive}"\n'
        f'claude_home = "{claude}"\ncodex_home = "{tmp_path / "absent"}"\n',
    )
    assert main(["run"]) == 0
    assert (archive / "dirac" / "claude" / "history.jsonl").read_bytes() == b"{}\n"


def test_run_strict_exits_three_when_the_destination_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "nas"
    archive.mkdir()
    write_config(tmp_path, monkeypatch, f'host = "dirac"\ndest_root = "{archive}"\n')
    assert main(["run", "--strict"]) == 3
    assert main(["run"]) == 0


def test_run_reports_configuration_errors_and_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(tmp_path, monkeypatch, 'host = "My_Mac"\n')
    assert main(["run"]) == 2
    assert "configuration error" in capsys.readouterr().err
