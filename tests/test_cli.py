"""Tests for the agent-session-backup command-line interface."""

import runpy
import sys

import pytest

from agent_session_backup import __version__
from agent_session_backup.cli import main


def test_main_succeeds_without_arguments() -> None:
    assert main([]) == 0


def test_version_flag_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"agent-session-backup {__version__}\n"


def test_module_entry_point_runs_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["agent-session-backup"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("agent_session_backup", run_name="__main__")
    assert exit_info.value.code == 0
