"""Tests for the run lock."""

import errno
import multiprocessing
from pathlib import Path
from typing import Any

import pytest

from agent_session_backup import lock
from agent_session_backup.lock import LOCK_NAME, LockBusy, run_lock

ACQUIRED_EXIT = 10
BUSY_EXIT = 20


def _try_lock(state_dir: str) -> None:
    """Child process body. Exit with one status for each outcome."""
    try:
        with run_lock(Path(state_dir)):
            raise SystemExit(ACQUIRED_EXIT)
    except LockBusy:
        raise SystemExit(BUSY_EXIT) from None


def _child_exit_code(state_dir: Path) -> int:
    process = multiprocessing.Process(target=_try_lock, args=(str(state_dir),))
    process.start()
    process.join(timeout=30)
    exitcode = process.exitcode
    assert isinstance(exitcode, int)
    return exitcode


def test_child_process_is_refused_while_the_parent_holds_the_lock(tmp_path: Path) -> None:
    with run_lock(tmp_path):
        assert _child_exit_code(tmp_path) == BUSY_EXIT


def test_child_process_acquires_the_lock_after_the_parent_releases(tmp_path: Path) -> None:
    with run_lock(tmp_path):
        pass
    assert _child_exit_code(tmp_path) == ACQUIRED_EXIT


def test_second_acquisition_in_the_same_process_raises_lock_busy(tmp_path: Path) -> None:
    with run_lock(tmp_path), pytest.raises(LockBusy), run_lock(tmp_path):
        pass


def test_exception_inside_the_block_releases_the_lock(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError), run_lock(tmp_path):
        raise RuntimeError("boom")
    with run_lock(tmp_path):
        pass


def test_lock_file_is_never_deleted(tmp_path: Path) -> None:
    with run_lock(tmp_path):
        pass
    assert (tmp_path / LOCK_NAME).is_file()


def test_a_lock_error_other_than_contention_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_acquire(handle: Any) -> None:
        raise OSError(errno.EIO, "simulated I/O error")

    monkeypatch.setattr(lock, "_acquire", failing_acquire)
    with pytest.raises(OSError, match="simulated I/O error") as raised, run_lock(tmp_path):
        pass
    assert not isinstance(raised.value, LockBusy)
