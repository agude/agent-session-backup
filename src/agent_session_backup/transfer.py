"""Decide whether a file needs copying, and copy it without deleting the destination."""

import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

TOLERANCE_SECONDS = 2.0
CHUNK_SIZE = 1024 * 1024


class Action(Enum):
    """What to do with one source file."""

    SKIP = "skip"
    COPY = "copy"
    PRESERVE_AND_COPY = "preserve_and_copy"


class MtimeNotKeptError(OSError):
    """The filesystem did not keep the mtime that was set on a file."""


@dataclass(frozen=True)
class Outcome:
    """What ``apply`` did: the bytes written and the .bak file it kept, if any."""

    bytes_copied: int
    backup: Path | None


def decide(src: os.stat_result, dst: os.stat_result | None) -> Action:
    """Return the action for a source file and its destination (``None`` if missing).

    The copy rules are:

    - Missing destination: copy.
    - Smaller destination: copy.
    - Same size, mtimes within 2 s (inclusive): skip.
    - Same size, source newer by more than 2 s: copy.
    - Same size, destination newer by more than 2 s: preserve the destination
      as a .bak file, then copy.
    - Larger destination: preserve the destination as a .bak file, then copy.
    """
    if dst is None or dst.st_size < src.st_size:
        return Action.COPY
    if dst.st_size > src.st_size:
        return Action.PRESERVE_AND_COPY
    delta = src.st_mtime - dst.st_mtime
    if abs(delta) <= TOLERANCE_SECONDS:
        return Action.SKIP
    if delta > TOLERANCE_SECONDS:
        return Action.COPY
    return Action.PRESERVE_AND_COPY


def apply(src_path: Path, dst_path: Path, action: Action, now: datetime) -> Outcome:
    """Carry out an action. ``now`` is UTC and names the .bak file.

    The source is streamed into a temporary sibling, which is fsynced and
    renamed over the destination. The destination then gets the source mtime.
    The destination changes only after the source has been read in full, so a
    failed read leaves it untouched.

    Raise ``MtimeNotKeptError`` after the copy is complete if the filesystem
    did not keep an mtime, on the destination or on the .bak file. The bytes
    are in place either way.
    """
    if action is Action.SKIP:
        return Outcome(bytes_copied=0, backup=None)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    _remove_stale_temporaries(dst_path)
    temporary = _temporary_path(dst_path)
    try:
        copied, mtime_ns = _write_temporary(src_path, temporary)
        backup = _preserve(dst_path, now) if action is Action.PRESERVE_AND_COPY else None
        unkept = [] if _rename_keeping_mtime(temporary, dst_path, mtime_ns) else [dst_path]
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    if backup is not None and not backup.kept_mtime:
        unkept.insert(0, backup.path)
    if unkept:
        names = ", ".join(str(path) for path in unkept)
        raise MtimeNotKeptError(f"the filesystem did not keep the mtime of {names}")
    return Outcome(bytes_copied=copied, backup=None if backup is None else backup.path)


def _rename_keeping_mtime(source: Path, target: Path, mtime_ns: int) -> bool:
    """Rename ``source`` to ``target`` and give ``target`` the mtime ``mtime_ns``.

    The mtime is set after the rename, because a rename on a CIFS mount resets
    it. Return whether the filesystem kept the mtime, within the copy tolerance.
    """
    source.replace(target)
    os.utime(target, ns=(mtime_ns, mtime_ns))
    kept_ns = target.stat().st_mtime_ns
    return abs(kept_ns - mtime_ns) <= TOLERANCE_SECONDS * 1_000_000_000


def _temporary_path(dst_path: Path) -> Path:
    return dst_path.with_name(f".{dst_path.name}.{os.getpid()}.tmp")


def _remove_stale_temporaries(dst_path: Path) -> None:
    """Remove temporary siblings that an interrupted copy of ``dst_path`` left behind."""
    prefix = f".{dst_path.name}."
    for sibling in dst_path.parent.iterdir():
        if sibling.name.startswith(prefix) and sibling.name.endswith(".tmp"):
            sibling.unlink(missing_ok=True)


def _write_temporary(src_path: Path, temporary: Path) -> tuple[int, int]:
    """Copy the source into ``temporary`` in one read.

    Return the bytes written and the source mtime in nanoseconds, taken when
    the source was opened.
    """
    copied = 0
    with src_path.open("rb") as source:
        source_stat = os.fstat(source.fileno())
        with temporary.open("wb") as target:
            while chunk := source.read(CHUNK_SIZE):
                target.write(chunk)
                copied += len(chunk)
            target.flush()
            os.fsync(target.fileno())
    return copied, source_stat.st_mtime_ns


@dataclass(frozen=True)
class _Backup:
    path: Path
    kept_mtime: bool


def _preserve(dst_path: Path, now: datetime) -> _Backup:
    """Rename the destination to its timestamped .bak name, keeping its mtime."""
    backup = dst_path.with_name(f"{dst_path.name}.{now:%Y%m%dT%H%M%SZ}.bak")
    if backup.exists():
        raise FileExistsError(f"backup already exists: {backup}")
    mtime_ns = dst_path.stat().st_mtime_ns
    return _Backup(path=backup, kept_mtime=_rename_keeping_mtime(dst_path, backup, mtime_ns))
