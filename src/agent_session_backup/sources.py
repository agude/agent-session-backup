"""Enumerate the files the backup copies, from an explicit allowlist."""

import fnmatch
import os
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from agent_session_backup.config import Config

# Patterns are relative to a tool's home directory. Each segment is a shell-style
# glob, except that "**" matches any number of directories, including none.
CLAUDE_PATTERNS: tuple[str, ...] = (
    "projects/**/*.jsonl",
    "projects/*/memory/**/*.md",
    "history.jsonl",
)
CODEX_PATTERNS: tuple[str, ...] = (
    "sessions/**/*.jsonl",
    "archived_sessions/**/*.jsonl",
    "history.jsonl",
    "session_index.jsonl",
)
# The Claude desktop app's per-session metadata (titles, folders, settings), relative
# to its data directory. Its transcripts are under CLAUDE_PATTERNS.
CLAUDE_DESKTOP_PATTERNS: tuple[str, ...] = ("claude-code-sessions/**/*.json",)
# The Codex thread databases. The Codex desktop app keeps conversations here after
# their rollout files are gone. They are copied as consistent snapshots, never as
# live files.
CODEX_SQLITE_PATTERNS: tuple[str, ...] = (
    "state_*.sqlite",
    "thread_history_*.sqlite",
)

ErrorCallback = Callable[[Path, OSError], None]
Segments = tuple[str, ...]


@dataclass(frozen=True)
class SourceFile:
    """A file to copy.

    ``path`` is the absolute source path. ``relpath`` is the destination path under
    the host directory, such as ``claude/projects/x/y.jsonl``. ``snapshot`` marks a
    SQLite database, which is copied through a consistent snapshot.
    """

    path: Path
    relpath: PurePosixPath
    snapshot: bool = False


def iter_sources(config: Config, on_error: ErrorCallback | None = None) -> Iterator[SourceFile]:
    """Yield every allowlisted file, sorted by relpath so that runs are deterministic.

    A missing tool home, or a disabled source, yields nothing. A directory that
    cannot be read is passed to ``on_error``, and enumeration continues.
    """
    found: list[SourceFile] = []
    if config.sources.claude:
        found += _enumerate(config.claude_home, "claude", CLAUDE_PATTERNS, on_error)
    if config.sources.claude_desktop and config.claude_desktop_home is not None:
        found += _enumerate(
            config.claude_desktop_home, "claude-desktop", CLAUDE_DESKTOP_PATTERNS, on_error
        )
    if config.sources.codex:
        found += _enumerate(config.codex_home, "codex", CODEX_PATTERNS, on_error)
    if config.sources.codex_sqlite:
        found += _enumerate(
            config.codex_home, "codex", CODEX_SQLITE_PATTERNS, on_error, snapshot=True
        )
    yield from sorted(found, key=lambda source: source.relpath.as_posix())


def _enumerate(
    home: Path,
    prefix: str,
    patterns: Sequence[str],
    on_error: ErrorCallback | None,
    *,
    snapshot: bool = False,
) -> list[SourceFile]:
    if not home.is_dir():
        return []
    segment_patterns = [tuple(pattern.split("/")) for pattern in patterns]
    return [
        SourceFile(path=path, relpath=PurePosixPath(prefix, *segments), snapshot=snapshot)
        for path, segments in _walk(home.absolute(), (), segment_patterns, on_error)
    ]


def _walk(
    directory: Path,
    relative: Segments,
    patterns: Sequence[Segments],
    on_error: ErrorCallback | None,
) -> Iterator[tuple[Path, Segments]]:
    """Yield the absolute path and the relative segments of each matching file.

    Only directories that could hold a match are opened. Symbolic links, FIFOs,
    and sockets are never followed or yielded.
    """
    try:
        children = list(os.scandir(directory))
    except OSError as error:
        _report(on_error, directory, error)
        return
    for child in children:
        segments = (*relative, child.name)
        child_path = Path(child.path)
        try:
            if child.is_dir(follow_symlinks=False) and _may_descend(segments, patterns):
                yield from _walk(child_path, segments, patterns, on_error)
            elif child.is_file(follow_symlinks=False) and _is_source(segments, patterns):
                yield child_path, segments
        except OSError as error:
            _report(on_error, child_path, error)


def _may_descend(directory: Segments, patterns: Sequence[Segments]) -> bool:
    return any(_could_match_below(directory, pattern) for pattern in patterns)


def _is_source(path: Segments, patterns: Sequence[Segments]) -> bool:
    return any(_matches(path, pattern) for pattern in patterns)


def _could_match_below(directory: Segments, pattern: Segments) -> bool:
    """Return whether some path below ``directory`` could match ``pattern``."""
    if not pattern:
        return False
    if pattern[0] == "**" or not directory:
        return True
    return fnmatch.fnmatchcase(directory[0], pattern[0]) and _could_match_below(
        directory[1:], pattern[1:]
    )


def _matches(path: Segments, pattern: Segments) -> bool:
    """Return whether the relative path matches the pattern."""
    if not pattern:
        return not path
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        return any(_matches(path[index:], rest) for index in range(len(path) + 1))
    return bool(path) and fnmatch.fnmatchcase(path[0], head) and _matches(path[1:], rest)


def _report(on_error: ErrorCallback | None, path: Path, error: OSError) -> None:
    if on_error is not None:
        on_error(path, error)
