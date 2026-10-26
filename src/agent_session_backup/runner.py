"""One backup pass: take the lock, check the destination, copy, and record the outcome."""

import json
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from agent_session_backup import __version__, destination
from agent_session_backup.config import Config
from agent_session_backup.lock import LockBusy, run_lock
from agent_session_backup.sources import SourceFile, iter_sources
from agent_session_backup.transfer import Action, apply, decide

LOGGER_NAME = "agent_session_backup"
LOG_FILE_NAME = "backup.log"
LAST_RUN_NAME = ".last-run.json"
MAX_LOG_BYTES = 1024 * 1024
LOG_BACKUP_COUNT = 3
MAX_RECORDED_ERRORS = 20
EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_UNAVAILABLE = 3

logger = logging.getLogger(LOGGER_NAME)


@dataclass
class Tally:
    """Counts and errors for one pass."""

    copied: int = 0
    preserved: int = 0
    skipped: int = 0
    bytes_copied: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)


def utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(UTC)


def configure_logging(state_dir: Path, *, verbose: bool) -> None:
    """Send log records to <state>/backup.log and to stderr.

    The log file gets INFO records, or DEBUG records when ``verbose`` is set.
    stderr gets only WARNING and above, so cron mails only problems. A later
    call replaces the handlers of an earlier one.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    file_handler = RotatingFileHandler(
        state_dir / LOG_FILE_NAME,
        maxBytes=MAX_LOG_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.WARNING)
    console.setFormatter(logging.Formatter("agent-session-backup: %(levelname)s: %(message)s"))
    logger.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    logger.addHandler(console)


def run_pass(
    config: Config,
    state_dir: Path,
    *,
    strict: bool = False,
    now: Callable[[], datetime] = utc_now,
) -> int:
    """Perform one backup pass and return its exit status.

    If another run holds the lock, the pass copies nothing and returns 0. With
    ``strict``, an unavailable destination returns 3 instead of 0.
    """
    started = now()
    try:
        with run_lock(state_dir):
            return _run_locked(config, started, now, strict=strict)
    except LockBusy:
        logger.info("another run is in progress; nothing copied")
        return EXIT_OK


def check_pass(config: Config, *, verbose: bool = False) -> int:
    """Report what a run would copy, taking no lock and writing nothing.

    Only ``decide()`` runs, so no file is read or written. Return 3 when the
    destination is unavailable and 0 otherwise. Per-file errors are printed and
    counted, but they do not change the exit status.
    """
    print(f"host: {config.host}")
    print(f"dest_root: {config.dest_root}")
    print(
        f"claude_home: {config.claude_home} ({'enabled' if config.sources.claude else 'disabled'})"
    )
    print(f"codex_home: {config.codex_home} ({'enabled' if config.sources.codex else 'disabled'})")
    status = destination.check(config.dest_root)
    if isinstance(status, destination.Unavailable):
        print(f"destination: unavailable: {status.reason}")
        return EXIT_UNAVAILABLE
    print("destination: available")
    host_dir = config.dest_root / config.host
    counts = dict.fromkeys(Action, 0)
    errors: list[tuple[str, str]] = []

    def record_error(path: Path, error: OSError) -> None:
        errors.append((str(path), str(error)))

    for source in iter_sources(config, record_error):
        try:
            action = decide(source.path.stat(), _stat_or_none(host_dir / source.relpath))
        except OSError as error:
            errors.append((str(source.path), str(error)))
            continue
        counts[action] += 1
        if verbose and action is not Action.SKIP:
            print(f"{action.value} {source.relpath.as_posix()}")
    for action in Action:
        print(f"{action.value}: {counts[action]}")
    print(f"errors: {len(errors)}")
    for path_text, message in errors:
        print(f"error {path_text}: {message}")
    return EXIT_OK


def _run_locked(
    config: Config, started: datetime, now: Callable[[], datetime], *, strict: bool
) -> int:
    status = destination.check(config.dest_root)
    if isinstance(status, destination.Unavailable):
        if strict:
            logger.warning("destination unavailable: %s", status.reason)
            return EXIT_UNAVAILABLE
        logger.info("destination unavailable: %s", status.reason)
        return EXIT_OK
    tally = Tally()
    host_dir = config.dest_root / config.host

    def record_error(path: Path, error: OSError) -> None:
        _record_error(tally, path, error)

    for source in iter_sources(config, record_error):
        _copy(source, host_dir, started, tally)
    finished = now()
    _write_last_run(host_dir, config.host, started, finished, tally)
    logger.info(
        "run %s: copied=%d preserved=%d skipped=%d errors=%d bytes=%d",
        "partial" if tally.errors else "ok",
        tally.copied,
        tally.preserved,
        tally.skipped,
        len(tally.errors),
        tally.bytes_copied,
    )
    return EXIT_PARTIAL if tally.errors else EXIT_OK


def _copy(source: SourceFile, host_dir: Path, started: datetime, tally: Tally) -> None:
    target = host_dir / source.relpath
    try:
        action = decide(source.path.stat(), _stat_or_none(target))
        if action is Action.SKIP:
            tally.skipped += 1
            return
        outcome = apply(source.path, target, action, started)
    except OSError as error:
        _record_error(tally, source.path, error)
        return
    if action is Action.COPY:
        tally.copied += 1
    else:
        tally.preserved += 1
    tally.bytes_copied += outcome.bytes_copied
    logger.debug("%s %s (%d bytes)", action.value, source.relpath.as_posix(), outcome.bytes_copied)
    if outcome.backup is not None:
        logger.debug("kept the previous copy as %s", outcome.backup)


def _stat_or_none(path: Path) -> os.stat_result | None:
    try:
        return path.stat()
    except FileNotFoundError:
        return None


def _record_error(tally: Tally, path: Path, error: Exception) -> None:
    tally.errors.append((str(path), str(error)))
    logger.warning("error on %s: %s", path, error)


def _write_last_run(
    host_dir: Path, host: str, started: datetime, finished: datetime, tally: Tally
) -> None:
    """Write <host>/.last-run.json atomically. A partial run keeps the previous last_success."""
    host_dir.mkdir(parents=True, exist_ok=True)
    record_path = host_dir / LAST_RUN_NAME
    partial = bool(tally.errors)
    record = {
        "host": host,
        "version": __version__,
        "started": _iso(started),
        "finished": _iso(finished),
        "status": "partial" if partial else "ok",
        "last_success": _last_success(record_path, finished, partial=partial),
        "counts": {
            "copied": tally.copied,
            "preserved": tally.preserved,
            "skipped": tally.skipped,
            "errors": len(tally.errors),
        },
        "bytes_copied": tally.bytes_copied,
        "errors": [
            {"path": error_path, "error": message}
            for error_path, message in tally.errors[:MAX_RECORDED_ERRORS]
        ],
    }
    _write_atomically(record_path, json.dumps(record, indent=2) + "\n")


def _last_success(record_path: Path, finished: datetime, *, partial: bool) -> str | None:
    if not partial:
        return _iso(finished)
    previous = _read_previous(record_path)
    value = None if previous is None else previous.get("last_success")
    return value if isinstance(value, str) else None


def _read_previous(record_path: Path) -> dict[str, object] | None:
    try:
        data: object = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_atomically(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
