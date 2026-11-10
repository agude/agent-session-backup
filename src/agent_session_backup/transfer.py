"""Decide whether a file needs copying, and copy it without deleting the destination."""

import os
import re
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
    """What ``apply`` did: the bytes written and the .bak file it kept, if any.

    ``identical`` is true when a preserve found the destination already held the
    source bytes, so ``apply`` only set its mtime and wrote nothing.
    """

    bytes_copied: int
    backup: Path | None
    identical: bool = False


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

    ``apply`` refines a same-size preserve: if the bytes are identical, it only
    sets the destination mtime.
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

    A preserve whose destination holds the same bytes as the source keeps no
    .bak file and copies nothing; it gives the destination the source mtime.
    That case arises when a filesystem did not keep an earlier copy's mtime,
    and without it every run would add another identical .bak file.

    Raise ``MtimeNotKeptError`` after the copy is complete if the filesystem
    could not set, verify, or keep an mtime, on the destination or on the
    .bak file. The bytes are in place either way.
    """
    if action is Action.SKIP:
        return Outcome(bytes_copied=0, backup=None)
    if action is Action.PRESERVE_AND_COPY:
        same_mtime_ns = _mtime_if_same_bytes(src_path, dst_path)
        if same_mtime_ns is not None:
            if not _set_mtime(dst_path, same_mtime_ns):
                raise MtimeNotKeptError(f"the filesystem did not keep the mtime of {dst_path}")
            return Outcome(bytes_copied=0, backup=None, identical=True)
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
    Metadata errors return False so a .bak timestamp failure cannot prevent
    the new destination from being installed. Rename errors still propagate.
    """
    source.replace(target)
    return _set_mtime(target, mtime_ns)


def _set_mtime(target: Path, mtime_ns: int) -> bool:
    """Give ``target`` the mtime ``mtime_ns``. Return whether it was kept, within tolerance."""
    try:
        os.utime(target, ns=(mtime_ns, mtime_ns))
        kept_ns = target.stat().st_mtime_ns
    except OSError:
        return False
    return abs(kept_ns - mtime_ns) <= TOLERANCE_SECONDS * 1_000_000_000


def _mtime_if_same_bytes(src_path: Path, dst_path: Path) -> int | None:
    """Return the source mtime in nanoseconds if both files hold the same bytes, else None.

    The mtime is taken from the open source before any byte is read. A source
    that changes during the comparison then ends up newer than that mtime, so
    the next run copies it instead of skipping a stale destination.
    A missing destination differs.
    """
    try:
        dst_size = dst_path.stat().st_size
    except FileNotFoundError:
        return None
    with src_path.open("rb") as source:
        source_stat = os.fstat(source.fileno())
        if source_stat.st_size != dst_size:
            return None
        with dst_path.open("rb") as target:
            while chunk := source.read(CHUNK_SIZE):
                if chunk != target.read(len(chunk)):
                    return None
            if target.read(1) != b"":
                return None
    return source_stat.st_mtime_ns


def _temporary_path(dst_path: Path) -> Path:
    return dst_path.with_name(f".{dst_path.name}.{os.getpid()}.tmp")


def _remove_stale_temporaries(dst_path: Path) -> None:
    """Remove temporary siblings that an interrupted copy of ``dst_path`` left behind.

    Only names that ``_temporary_path`` makes match: the destination name and a
    process id. Any other file is left alone.
    """
    own = re.compile(rf"\.{re.escape(dst_path.name)}\.[0-9]+\.tmp")
    for sibling in dst_path.parent.iterdir():
        if own.fullmatch(sibling.name):
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
