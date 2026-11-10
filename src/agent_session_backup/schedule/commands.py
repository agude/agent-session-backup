"""Run scheduler commands through an injectable runner, so that tests can replace them."""

import locale
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

MISSING_PROGRAM_STATUS = 127


class ScheduleError(Exception):
    """A scheduler cannot be used, or its state is damaged."""


@dataclass(frozen=True)
class CommandResult:
    """The exit status and captured output of one command."""

    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str], str | None], CommandResult]


def run_command(args: Sequence[str], stdin: str | None = None) -> CommandResult:
    """Run ``args``, feeding ``stdin`` if it is given. A missing program reports status 127.

    The pipes carry bytes, and this function encodes and decodes them itself.
    Windows console programs such as schtasks write the OEM code page, not the
    ANSI code page that text mode would assume; undecodable bytes become U+FFFD.
    Elsewhere the locale encoding is used with surrogate escapes, so a crontab
    read and written back keeps every byte, including bytes the locale cannot
    decode.
    """
    encoding, errors = _pipe_encoding()
    data = None if stdin is None else stdin.encode(encoding, errors)
    try:
        completed = subprocess.run(list(args), input=data, capture_output=True, check=False)
    except OSError as error:
        return CommandResult(MISSING_PROGRAM_STATUS, "", str(error))
    return CommandResult(
        completed.returncode,
        _decode(completed.stdout, encoding, errors),
        _decode(completed.stderr, encoding, errors),
    )


def _pipe_encoding() -> tuple[str, str]:
    """Return the codec and error handler for a child process's pipes."""
    if sys.platform == "win32":
        return "oem", "replace"
    return locale.getpreferredencoding(False), "surrogateescape"


def _decode(data: bytes, encoding: str, errors: str) -> str:
    """Decode a pipe. On Windows, CRLF line endings become LF, as text mode did."""
    text = data.decode(encoding, errors)
    if sys.platform == "win32":
        text = text.replace("\r\n", "\n")
    return text
