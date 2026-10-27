"""Manage the agent-session-backup block in the user's crontab on Linux."""

from agent_session_backup.schedule.commands import Runner, ScheduleError, run_command

BEGIN = "# BEGIN agent-session-backup (managed by `agent-session-backup install-schedule`)"
END = "# END agent-session-backup"
_NO_CRONTAB = "no crontab for"


class CronError(ScheduleError):
    """The crontab cannot be read or written, or its managed block is damaged."""


def install(executable: str, runner: Runner = run_command, *, dry_run: bool = False) -> str:
    """Add the hourly block, or update it to run ``executable``.

    Return ``installed``, ``updated``, or ``unchanged``. Lines outside the block
    are kept byte for byte, and the crontab is written only when it changes.
    With ``dry_run``, return the same result and write nothing.
    """
    current = _read(runner)
    block = _block(executable)
    span = _find_block(current)
    if span is None:
        new = _insert(current, block)
        result = "installed"
    else:
        new = _replace(current, span, block)
        result = "updated"
    if new == current:
        return "unchanged"
    if not dry_run:
        _write(new, runner)
    return result


def uninstall(runner: Runner = run_command, *, dry_run: bool = False) -> str:
    """Remove the managed block. Return ``removed``, or ``absent`` if there is none.

    With ``dry_run``, return the same result and write nothing.
    """
    current = _read(runner)
    span = _find_block(current)
    if span is None:
        return "absent"
    if dry_run:
        return "removed"
    start, end = span
    before, after = current[:start], current[end:]
    # install() put a newline before a block that ends the file without one.
    # Remove that newline so that the round trip restores the original bytes.
    if after == "" and before.endswith("\n") and not current[start:end].endswith("\n"):
        before = before[:-1]
    _write(before + after, runner)
    return "removed"


def _block(executable: str) -> str:
    return "\n".join((BEGIN, f"@hourly {executable} run", END))


def _insert(current: str, block: str) -> str:
    if current == "":
        return block + "\n"
    if current.endswith("\n"):
        return current + block + "\n"
    return current + "\n" + block


def _replace(current: str, span: tuple[int, int], block: str) -> str:
    start, end = span
    ending = "\n" if current[start:end].endswith("\n") else ""
    return current[:start] + block + ending + current[end:]


def _find_block(text: str) -> tuple[int, int] | None:
    """Return the offsets of the managed block, from its BEGIN line through its END line."""
    begins: list[int] = []
    ends: list[int] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        marker = line.rstrip("\r\n")
        if marker == BEGIN:
            begins.append(offset)
        elif marker == END:
            ends.append(offset + len(line))
        offset += len(line)
    if not begins and not ends:
        return None
    if len(begins) > 1 or len(ends) > 1:
        raise CronError("the crontab has more than one agent-session-backup block")
    if len(begins) != len(ends):
        raise CronError("the crontab has an unterminated agent-session-backup block")
    if ends[0] <= begins[0]:
        raise CronError("the agent-session-backup block ends before it begins")
    return begins[0], ends[0]


def _read(runner: Runner) -> str:
    result = runner(["crontab", "-l"], None)
    if result.returncode == 0:
        return result.stdout
    if result.returncode == 1 and _NO_CRONTAB in result.stderr:
        return ""
    raise CronError(f"crontab -l failed: {result.stderr.strip()}")


def _write(text: str, runner: Runner) -> None:
    result = runner(["crontab", "-"], text)
    if result.returncode != 0:
        raise CronError(f"crontab - failed: {result.stderr.strip()}")
