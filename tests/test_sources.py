"""Tests for the source file allowlist."""

import os
from pathlib import Path

import pytest

from agent_session_backup.config import Config, Sources
from agent_session_backup.sources import iter_sources

CLAUDE_FILES = (
    "projects/a/s1.jsonl",
    "projects/a/s1/subagents/t.jsonl",
    "projects/a/memory/MEMORY.md",
    "projects/a/notes.txt",
    "history.jsonl",
    "settings.json",
    ".credentials.json",
    "file-history/x",
    "todos/x.json",
)
CODEX_FILES = (
    "sessions/2026/10/26/rollout-x.jsonl",
    "archived_sessions/rollout-y.jsonl",
    "history.jsonl",
    "session_index.jsonl",
    "auth.json",
    "state_5.sqlite",
)
CLAUDE_SOURCES = [
    "claude/history.jsonl",
    "claude/projects/a/memory/MEMORY.md",
    "claude/projects/a/s1.jsonl",
    "claude/projects/a/s1/subagents/t.jsonl",
]
CODEX_SOURCES = [
    "codex/archived_sessions/rollout-y.jsonl",
    "codex/history.jsonl",
    "codex/session_index.jsonl",
    "codex/sessions/2026/10/26/rollout-x.jsonl",
]
EXCLUDED_PARTS = frozenset(
    {"auth.json", ".credentials.json", "state_5.sqlite", "file-history", "todos", "settings.json"}
)


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n", encoding="utf-8")


@pytest.fixture
def homes(tmp_path: Path) -> tuple[Path, Path]:
    claude = tmp_path / "claude"
    codex = tmp_path / "codex"
    for name in CLAUDE_FILES:
        touch(claude / name)
    for name in CODEX_FILES:
        touch(codex / name)
    return claude, codex


def make_config(claude: Path, codex: Path, sources: Sources | None = None) -> Config:
    return Config(
        host="dirac",
        dest_root=claude.parent / "dest",
        claude_home=claude,
        codex_home=codex,
        sources=Sources() if sources is None else sources,
    )


def scan(config: Config) -> tuple[list[str], list[Path]]:
    """Return the relpaths in yield order and the paths reported to the error callback."""
    reported: list[Path] = []
    names = [
        source.relpath.as_posix()
        for source in iter_sources(config, lambda path, error: reported.append(path))
    ]
    return names, reported


def test_claude_fixture_yields_the_allowlisted_files(homes: tuple[Path, Path]) -> None:
    claude, codex = homes
    names, reported = scan(make_config(claude, codex, Sources(codex=False)))
    assert names == CLAUDE_SOURCES
    assert reported == []


def test_codex_fixture_yields_the_allowlisted_files(homes: tuple[Path, Path]) -> None:
    claude, codex = homes
    names, _ = scan(make_config(claude, codex, Sources(claude=False)))
    assert names == CODEX_SOURCES


def test_both_tools_yield_in_relpath_order(homes: tuple[Path, Path]) -> None:
    claude, codex = homes
    names, _ = scan(make_config(claude, codex))
    assert names == CLAUDE_SOURCES + CODEX_SOURCES


def test_excluded_files_are_never_yielded(homes: tuple[Path, Path]) -> None:
    claude, codex = homes
    names, _ = scan(make_config(claude, codex))
    for name in names:
        assert not EXCLUDED_PARTS.intersection(name.split("/")), name


def test_symlinked_file_and_directory_are_skipped(homes: tuple[Path, Path]) -> None:
    claude, codex = homes
    projects = claude / "projects" / "a"
    (projects / "link.jsonl").symlink_to(projects / "s1.jsonl")
    (projects / "linkdir").symlink_to(projects / "s1", target_is_directory=True)
    names, _ = scan(make_config(claude, codex))
    assert names == CLAUDE_SOURCES + CODEX_SOURCES


def test_fifos_are_skipped(homes: tuple[Path, Path]) -> None:
    claude, codex = homes
    os.mkfifo(claude / "projects" / "a" / "pipe.jsonl")
    names, _ = scan(make_config(claude, codex))
    assert names == CLAUDE_SOURCES + CODEX_SOURCES


def test_missing_codex_home_yields_only_claude_files(
    homes: tuple[Path, Path], tmp_path: Path
) -> None:
    claude, _ = homes
    names, reported = scan(make_config(claude, tmp_path / "absent"))
    assert names == CLAUDE_SOURCES
    assert reported == []


def test_disabled_codex_source_yields_only_claude_files(homes: tuple[Path, Path]) -> None:
    claude, codex = homes
    names, _ = scan(make_config(claude, codex, Sources(codex=False)))
    assert all(name.startswith("claude/") for name in names)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any directory")
def test_unreadable_directory_is_reported_and_enumeration_continues(
    homes: tuple[Path, Path],
) -> None:
    claude, codex = homes
    locked = claude / "projects" / "a" / "locked"
    touch(locked / "inner.jsonl")
    locked.chmod(0)
    try:
        names, reported = scan(make_config(claude, codex))
    finally:
        locked.chmod(0o700)
    assert reported == [locked]
    assert names == CLAUDE_SOURCES + CODEX_SOURCES


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any directory")
def test_unreadable_directory_without_callback_raises_nothing(homes: tuple[Path, Path]) -> None:
    claude, codex = homes
    locked = claude / "projects" / "a" / "locked"
    touch(locked / "inner.jsonl")
    locked.chmod(0)
    try:
        names = [source.relpath.as_posix() for source in iter_sources(make_config(claude, codex))]
    finally:
        locked.chmod(0o700)
    assert names == CLAUDE_SOURCES + CODEX_SOURCES


def test_entry_whose_type_cannot_be_read_is_reported(
    homes: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    claude, codex = homes
    broken = str(claude / "history.jsonl")
    real_scandir = os.scandir

    class FlakyEntry:
        """Wraps a real directory entry and fails when its type is requested."""

        def __init__(self, entry: os.DirEntry[str]) -> None:
            self.entry = entry
            self.name = entry.name
            self.path = entry.path

        def is_dir(self, *, follow_symlinks: bool = True) -> bool:
            if self.path == broken:
                raise PermissionError("cannot read the entry type")
            return self.entry.is_dir(follow_symlinks=follow_symlinks)

        def is_file(self, *, follow_symlinks: bool = True) -> bool:
            return self.entry.is_file(follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os, "scandir", lambda path: [FlakyEntry(e) for e in real_scandir(path)])
    names, reported = scan(make_config(claude, codex))
    assert reported == [claude / "history.jsonl"]
    assert "claude/history.jsonl" not in names
    assert "codex/history.jsonl" in names


def test_directory_named_like_a_file_pattern_is_not_descended(tmp_path: Path) -> None:
    claude = tmp_path / "claude"
    touch(claude / "history.jsonl" / "inner.jsonl")
    names, reported = scan(make_config(claude, tmp_path / "absent", Sources(codex=False)))
    assert names == []
    assert reported == []


def test_relpaths_stay_under_the_tool_directory(homes: tuple[Path, Path]) -> None:
    claude, codex = homes
    names, _ = scan(make_config(claude, codex))
    for name in names:
        parts = name.split("/")
        assert parts[0] in {"claude", "codex"}
        assert ".." not in parts


def test_source_paths_are_absolute_regular_files(homes: tuple[Path, Path]) -> None:
    claude, codex = homes
    for source in iter_sources(make_config(claude, codex)):
        assert source.path.is_absolute()
        assert source.path.is_file()
