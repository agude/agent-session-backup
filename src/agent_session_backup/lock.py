"""Prevent overlapping runs with an exclusive, non-blocking lock."""

import errno
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

LOCK_NAME = "run.lock"


class LockBusy(Exception):
    """Another process holds the run lock."""


@contextmanager
def run_lock(state_dir: Path) -> Iterator[None]:
    """Hold the run lock for the duration of the block.

    Raise ``LockBusy`` if another process holds it. Any other failure to open
    or lock the file propagates as OSError. The lock file is never deleted.
    Removing it would let a second run lock a new file while the first run
    still holds the old one.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / LOCK_NAME
    with lock_path.open("a+b") as handle:
        try:
            _acquire(handle)
        except OSError as error:
            if error.errno not in _BUSY_ERRNOS:
                raise
            raise LockBusy(f"another run holds {lock_path}") from error
        try:
            yield
        finally:
            _release(handle)


if sys.platform == "win32":
    import msvcrt
    import os

    # msvcrt.locking reports a byte that another process holds as EACCES; the C
    # runtime documents EDEADLOCK for the same case.
    _BUSY_ERRNOS = frozenset({errno.EACCES, errno.EDEADLOCK})

    def _acquire(handle: BinaryIO) -> None:
        """Lock byte 0. A lock cannot cover bytes past the end, so an empty file gets one."""
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _release(handle: BinaryIO) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    _BUSY_ERRNOS = frozenset({errno.EAGAIN, errno.EWOULDBLOCK})

    def _acquire(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _release(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
