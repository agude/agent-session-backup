"""Tests for copy decisions and atomic copies."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Any

import pytest

from agent_session_backup import transfer
from agent_session_backup.transfer import Action, Outcome, apply, decide

BASE_MTIME = 1_800_000_000.0
NOW = datetime(2026, 10, 26, 18, 0, 0, tzinfo=UTC)
BACKUP_NAME = "s1.jsonl.20261026T180000Z.bak"


def write(path: Path, data: bytes, mtime: float = BASE_MTIME) -> os.stat_result:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))
    return path.stat()


@pytest.mark.parametrize(
    ("dst_size", "delta", "expected"),
    [
        (None, 0.0, Action.COPY),
        (5, 0.0, Action.COPY),
        (10, 0.0, Action.SKIP),
        (10, 1.9, Action.SKIP),
        (10, 2.0, Action.SKIP),
        (10, 2.1, Action.COPY),
        (20, 0.0, Action.PRESERVE_AND_COPY),
    ],
)
def test_decide_covers_each_row_of_the_copy_rules(
    tmp_path: Path, dst_size: int | None, delta: float, expected: Action
) -> None:
    src = write(tmp_path / "src", b"s" * 10, BASE_MTIME + delta)
    dst = None if dst_size is None else write(tmp_path / "dst", b"d" * dst_size)
    assert decide(src, dst) is expected


def test_same_size_destination_2_1_s_newer_is_preserved(tmp_path: Path) -> None:
    src = write(tmp_path / "src", b"s" * 10, BASE_MTIME)
    dst = write(tmp_path / "dst", b"d" * 10, BASE_MTIME + 2.1)
    assert decide(src, dst) is Action.PRESERVE_AND_COPY


def test_same_size_destination_1_9_s_newer_is_skipped(tmp_path: Path) -> None:
    src = write(tmp_path / "src", b"s" * 10, BASE_MTIME)
    dst = write(tmp_path / "dst", b"d" * 10, BASE_MTIME + 1.9)
    assert decide(src, dst) is Action.SKIP


def test_copy_to_missing_destination_matches_source(tmp_path: Path) -> None:
    source = tmp_path / "src.jsonl"
    source_stat = write(source, b"hello\n")
    dst = tmp_path / "archive" / "claude" / "s1.jsonl"
    assert apply(source, dst, Action.COPY, NOW) == Outcome(bytes_copied=6, backup=None)
    assert dst.read_bytes() == b"hello\n"
    assert abs(dst.stat().st_mtime - source_stat.st_mtime) < 1


def test_decide_skips_the_pair_after_a_copy(tmp_path: Path) -> None:
    source = tmp_path / "src.jsonl"
    write(source, b"hello\n")
    dst = tmp_path / "dst" / "s1.jsonl"
    apply(source, dst, Action.COPY, NOW)
    assert decide(source.stat(), dst.stat()) is Action.SKIP


def test_skip_writes_nothing(tmp_path: Path) -> None:
    source = tmp_path / "src.jsonl"
    write(source, b"hello\n")
    dst = tmp_path / "dst" / "s1.jsonl"
    assert apply(source, dst, Action.SKIP, NOW) == Outcome(bytes_copied=0, backup=None)
    assert not dst.exists()


def test_preserve_and_copy_keeps_old_bytes_in_backup(tmp_path: Path) -> None:
    source = tmp_path / "src.jsonl"
    write(source, b"new\n")
    dst = tmp_path / "dst" / "s1.jsonl"
    write(dst, b"old and longer\n")
    outcome = apply(source, dst, Action.PRESERVE_AND_COPY, NOW)
    backup = dst.with_name(BACKUP_NAME)
    assert outcome == Outcome(bytes_copied=4, backup=backup)
    assert backup.read_bytes() == b"old and longer\n"
    assert dst.read_bytes() == b"new\n"


def test_two_preserves_with_different_times_keep_two_backups(tmp_path: Path) -> None:
    source = tmp_path / "src.jsonl"
    write(source, b"new\n")
    dst = tmp_path / "dst" / "s1.jsonl"
    write(dst, b"first and longer\n")
    first = apply(source, dst, Action.PRESERVE_AND_COPY, NOW).backup
    write(dst, b"second and longer\n")
    second = apply(source, dst, Action.PRESERVE_AND_COPY, NOW + timedelta(seconds=1)).backup
    assert first is not None
    assert second is not None
    assert first != second
    assert first.read_bytes() == b"first and longer\n"
    assert second.read_bytes() == b"second and longer\n"


def test_preserve_refuses_to_overwrite_an_existing_backup(tmp_path: Path) -> None:
    source = tmp_path / "src.jsonl"
    write(source, b"new\n")
    dst = tmp_path / "dst" / "s1.jsonl"
    write(dst.with_name(BACKUP_NAME), b"kept\n")
    write(dst, b"old and longer\n")
    with pytest.raises(FileExistsError, match="backup already exists"):
        apply(source, dst, Action.PRESERVE_AND_COPY, NOW)
    assert dst.with_name(BACKUP_NAME).read_bytes() == b"kept\n"
    assert dst.read_bytes() == b"old and longer\n"
    assert sorted(entry.name for entry in dst.parent.iterdir()) == ["s1.jsonl", BACKUP_NAME]


class _FailsAfterFirstChunk:
    """A binary reader that fails on its second read, as a read error partway through would."""

    def __init__(self, handle: IO[bytes]) -> None:
        self._handle = handle
        self._reads = 0

    def __enter__(self) -> "_FailsAfterFirstChunk":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._handle.close()

    def fileno(self) -> int:
        return self._handle.fileno()

    def read(self, size: int = -1) -> bytes:
        self._reads += 1
        if self._reads > 1:
            raise OSError("simulated read failure")
        return self._handle.read(size)


def test_failed_source_read_leaves_destination_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src.jsonl"
    write(source, b"x" * (2 * transfer.CHUNK_SIZE + 1))
    dst = tmp_path / "dst" / "s1.jsonl"
    write(dst, b"old\n")
    real_open = Path.open

    def open_source(self: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        handle = real_open(self, mode, *args, **kwargs)
        if self == source and mode == "rb":
            return _FailsAfterFirstChunk(handle)
        return handle

    monkeypatch.setattr(Path, "open", open_source)
    with pytest.raises(OSError, match="simulated read failure"):
        apply(source, dst, Action.COPY, NOW)
    monkeypatch.undo()
    assert dst.read_bytes() == b"old\n"
    assert sorted(entry.name for entry in dst.parent.iterdir()) == ["s1.jsonl"]


def test_stale_temporary_sibling_is_removed(tmp_path: Path) -> None:
    source = tmp_path / "src.jsonl"
    write(source, b"hello\n")
    dst = tmp_path / "dst" / "s1.jsonl"
    stale = dst.with_name(".s1.jsonl.999.tmp")
    write(stale, b"stale")
    apply(source, dst, Action.COPY, NOW)
    assert not stale.exists()
    assert dst.read_bytes() == b"hello\n"


def test_only_temporary_files_are_unlinked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[str] = []
    real_unlink = os.unlink
    real_remove = os.remove
    real_path_unlink = Path.unlink

    def spy_unlink(path: Any, *args: Any, **kwargs: Any) -> None:
        recorded.append(os.fspath(path))
        real_unlink(path, *args, **kwargs)

    def spy_remove(path: Any, *args: Any, **kwargs: Any) -> None:
        recorded.append(os.fspath(path))
        real_remove(path, *args, **kwargs)

    def spy_path_unlink(self: Path, missing_ok: bool = False) -> None:
        recorded.append(str(self))
        real_path_unlink(self, missing_ok=missing_ok)

    source = tmp_path / "src.jsonl"
    write(source, b"new\n")
    copy_dst = tmp_path / "copy" / "s1.jsonl"
    preserve_dst = tmp_path / "preserve" / "s2.jsonl"
    write(copy_dst.with_name(".s1.jsonl.999.tmp"), b"stale")
    write(preserve_dst.with_name(".s2.jsonl.999.tmp"), b"stale")
    write(preserve_dst, b"old and longer\n")
    monkeypatch.setattr(os, "unlink", spy_unlink)
    monkeypatch.setattr(os, "remove", spy_remove)
    monkeypatch.setattr(Path, "unlink", spy_path_unlink)
    apply(source, copy_dst, Action.COPY, NOW)
    apply(source, preserve_dst, Action.PRESERVE_AND_COPY, NOW)
    assert recorded
    assert all(path.endswith(".tmp") for path in recorded)
