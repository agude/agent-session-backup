"""Tests for the run lock."""

import multiprocessing
from pathlib import Path

import pytest

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
    context = multiprocessing.get_context("fork")
    process = context.Process(target=_try_lock, args=(str(state_dir),))
    process.start()
    process.join(timeout=30)
    assert process.exitcode is not None
    return process.exitcode


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
