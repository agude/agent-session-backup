"""Copy a live SQLite database through a consistent snapshot.

A database that an app has open can change between the reads of a plain file copy,
and recent writes may sit in its write-ahead log (``<name>-wal``). SQLite's online
backup reads the database and its log as one consistent state.
"""

import os
import sqlite3
from pathlib import Path

from agent_session_backup.transfer import TOLERANCE_SECONDS, Action

WAL_SUFFIX = "-wal"


def source_mtime_ns(database: Path) -> int:
    """Return the newer mtime of the database and its write-ahead log, in nanoseconds."""
    mtime = database.stat().st_mtime_ns
    try:
        return max(mtime, Path(f"{database}{WAL_SUFFIX}").stat().st_mtime_ns)
    except FileNotFoundError:
        return mtime


def plan(source_mtime: int, destination: os.stat_result | None) -> Action:
    """Return SKIP when the destination already holds this state of the database, else COPY.

    A snapshot carries the source mtime, so a destination within the copy tolerance
    of it is current. ``apply`` may still preserve the old copy first, by the usual
    size and mtime rules, once the snapshot exists.
    """
    if destination is None:
        return Action.COPY
    delta = abs(source_mtime - destination.st_mtime_ns)
    return Action.SKIP if delta <= TOLERANCE_SECONDS * 1_000_000_000 else Action.COPY


def take(database: Path, output: Path) -> None:
    """Write a consistent snapshot of ``database`` to ``output``, with the source mtime.

    The source is opened read-only. The mtime is read before the backup, so a write
    during the backup leaves the source newer and the next run copies it again.
    """
    mtime = source_mtime_ns(database)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    source = sqlite3.connect(f"{database.absolute().as_uri()}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(output)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    os.utime(output, ns=(mtime, mtime))
