"""Prevent overlapping runs with an exclusive, non-blocking lock."""

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

LOCK_NAME = "run.lock"


class LockBusy(Exception):
    """Another process holds the run lock."""


@contextmanager
def run_lock(state_dir: Path) -> Iterator[None]:
    """Hold the run lock for the duration of the block.

    Raise ``LockBusy`` if another process holds it. The lock file is never
    deleted. Removing it would let a second run lock a new file while the first
    run still holds the old one.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / LOCK_NAME
    with lock_path.open("a+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise LockBusy(f"another run holds {lock_path}") from error
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
