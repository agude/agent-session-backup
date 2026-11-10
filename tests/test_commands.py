"""Tests for the scheduler command runner's pipe encoding."""

import subprocess
import sys
from typing import Any

import pytest

from agent_session_backup.schedule import commands
from agent_session_backup.schedule.commands import MISSING_PROGRAM_STATUS, run_command


class FakeRun:
    """Stands in for subprocess.run. Records the input and returns fixed bytes."""

    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.inputs: list[Any] = []

    def __call__(self, args: list[str], **keywords: Any) -> subprocess.CompletedProcess[bytes]:
        assert "text" not in keywords
        assert "encoding" not in keywords
        self.inputs.append(keywords.get("input"))
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, self.stderr)


def test_posix_pipes_round_trip_bytes_the_locale_cannot_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(commands, "_pipe_encoding", lambda: ("utf-8", "surrogateescape"))
    raw = b"@daily /bin/true # \xff\r\n"
    fake = FakeRun(stdout=raw)
    monkeypatch.setattr(subprocess, "run", fake)
    result = run_command(["crontab", "-l"])
    run_command(["crontab", "-"], result.stdout)
    assert fake.inputs == [None, raw]


def test_windows_output_is_decoded_from_the_oem_code_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    # cp850 is a common OEM code page; 0x81 is u-umlaut there and undefined in cp1252.
    monkeypatch.setattr(commands, "_pipe_encoding", lambda: ("cp850", "replace"))
    fake = FakeRun(stderr=b"FEHLER: f\x81r\r\nzweite\r\n", returncode=1)
    monkeypatch.setattr(subprocess, "run", fake)
    result = run_command(["schtasks", "/Query"])
    assert result.stderr == "FEHLER: f\u00fcr\nzweite\n"
    assert result.returncode == 1


@pytest.mark.parametrize(
    ("platform", "expected_errors"), [("win32", "replace"), ("linux", "surrogateescape")]
)
def test_pipe_encoding_follows_the_platform(
    monkeypatch: pytest.MonkeyPatch, platform: str, expected_errors: str
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    encoding, errors = commands._pipe_encoding()
    assert errors == expected_errors
    assert (encoding == "oem") == (platform == "win32")


def test_missing_program_reports_status_127(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError("no such program")

    monkeypatch.setattr(subprocess, "run", missing)
    result = run_command(["schtasks"])
    assert result.returncode == MISSING_PROGRAM_STATUS
    assert "no such program" in result.stderr
