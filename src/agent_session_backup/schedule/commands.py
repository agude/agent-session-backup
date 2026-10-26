"""Run scheduler commands through an injectable runner, so that tests can replace them."""

import subprocess
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
    """Run ``args``, feeding ``stdin`` if it is given. A missing program reports status 127."""
    try:
        completed = subprocess.run(
            list(args), input=stdin, capture_output=True, text=True, check=False
        )
    except OSError as error:
        return CommandResult(MISSING_PROGRAM_STATUS, "", str(error))
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)
