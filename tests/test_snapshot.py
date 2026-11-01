"""Tests for SQLite snapshots and the database sources in a backup pass."""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_session_backup import snapshot
from agent_session_backup.config import Config, Sources
from agent_session_backup.runner import SNAPSHOT_DIR_NAME, run_pass
from agent_session_backup.sources import iter_sources
from agent_session_backup.transfer import Action

HOST = "maxwell"
NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)


@pytest.fixture
def live_database(tmp_path: Path) -> Iterator[Path]:
    """A WAL-mode database held open by a writer, as the Codex app holds its databases."""
    path = tmp_path / "codex" / "thread_history_1.sqlite"
    path.parent.mkdir()
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("pragma journal_mode=wal")
        writer.execute("create table items (id integer primary key, text text)")
        writer.executemany("insert into items (text) values (?)", [("one",), ("two",)])
        writer.commit()
        yield path


def rows(path: Path) -> list[str]:
    with closing(sqlite3.connect(path)) as db:
        return [text for (text,) in db.execute("select text from items order by id")]


def test_take_copies_committed_rows_from_the_log_and_keeps_the_source_mtime(
    live_database: Path, tmp_path: Path
) -> None:
    assert Path(f"{live_database}-wal").is_file()
    output = tmp_path / "snapshots" / "thread_history_1.sqlite"
    snapshot.take(live_database, output)
    assert rows(output) == ["one", "two"]
    assert output.stat().st_mtime_ns == snapshot.source_mtime_ns(live_database)
    assert not Path(f"{output}-wal").exists()


def test_source_mtime_is_the_newer_of_the_database_and_its_log(tmp_path: Path) -> None:
    database = tmp_path / "state_5.sqlite"
    database.write_bytes(b"")
    os.utime(database, ns=(1_000_000_000_000, 1_000_000_000_000))
    assert snapshot.source_mtime_ns(database) == 1_000_000_000_000
    wal = tmp_path / "state_5.sqlite-wal"
    wal.write_bytes(b"")
    os.utime(wal, ns=(2_000_000_000_000, 2_000_000_000_000))
    assert snapshot.source_mtime_ns(database) == 2_000_000_000_000


def test_plan_skips_a_destination_within_the_tolerance(tmp_path: Path) -> None:
    destination = tmp_path / "copy"
    destination.write_bytes(b"x")
    os.utime(destination, ns=(10_000_000_000, 10_000_000_000))
    stat = destination.stat()
    assert snapshot.plan(10_000_000_000, None) is Action.COPY
    assert snapshot.plan(11_000_000_000, stat) is Action.SKIP
    assert snapshot.plan(13_000_000_000, stat) is Action.COPY
    assert snapshot.plan(7_000_000_000, stat) is Action.COPY


def make_config(tmp_path: Path, codex_home: Path, *, codex_sqlite: bool) -> Config:
    archive = tmp_path / "archive"
    archive.mkdir(exist_ok=True)
    (archive / ".agent-session-backup-root").write_text("{}\n", encoding="utf-8")
    return Config(
        host=HOST,
        dest_root=archive,
        claude_home=tmp_path / "absent-claude",
        codex_home=codex_home,
        sources=Sources(codex_sqlite=codex_sqlite),
    )


def test_sources_list_the_databases_only_when_enabled(live_database: Path, tmp_path: Path) -> None:
    home = live_database.parent
    (home / "logs_2.sqlite").write_bytes(b"")
    enabled = list(iter_sources(make_config(tmp_path, home, codex_sqlite=True)))
    assert [(s.relpath.as_posix(), s.snapshot) for s in enabled] == [
        ("codex/thread_history_1.sqlite", True)
    ]
    assert list(iter_sources(make_config(tmp_path, home, codex_sqlite=False))) == []


def test_a_pass_snapshots_a_changed_database_and_skips_an_unchanged_one(
    live_database: Path, tmp_path: Path
) -> None:
    config = make_config(tmp_path, live_database.parent, codex_sqlite=True)
    state = tmp_path / "state"
    copied = tmp_path / "archive" / HOST / "codex" / "thread_history_1.sqlite"

    assert run_pass(config, state, now=lambda: NOW) == 0
    assert rows(copied) == ["one", "two"]
    snapshot_path = state / SNAPSHOT_DIR_NAME / "codex" / "thread_history_1.sqlite"
    first = snapshot_path.stat().st_mtime_ns

    os.utime(live_database, ns=(first, first))
    Path(f"{live_database}-wal").touch()
    os.utime(Path(f"{live_database}-wal"), ns=(first, first))
    assert run_pass(config, state, now=lambda: NOW) == 0
    assert snapshot_path.stat().st_mtime_ns == first

    with closing(sqlite3.connect(live_database)) as writer:
        writer.execute("insert into items (text) values ('three')")
        writer.commit()
    later = first + 10_000_000_000
    os.utime(Path(f"{live_database}-wal"), ns=(later, later))
    assert run_pass(config, state, now=lambda: NOW) == 0
    assert rows(copied) == ["one", "two", "three"]


def test_an_unreadable_database_is_a_recorded_error(tmp_path: Path) -> None:
    home = tmp_path / "codex"
    home.mkdir()
    (home / "state_5.sqlite").write_bytes(b"not a database, just bytes long enough")
    config = make_config(tmp_path, home, codex_sqlite=True)
    assert run_pass(config, tmp_path / "state", now=lambda: NOW) == 1
