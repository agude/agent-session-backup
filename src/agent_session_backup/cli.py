"""Command-line interface for agent-session-backup."""

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from datetime import timedelta
from pathlib import Path

from agent_session_backup import __version__, destination, paths, runner, schedule, status
from agent_session_backup.config import ConfigError, load
from agent_session_backup.schedule.commands import ScheduleError

DESCRIPTION = (
    "Hourly archive of Claude Code and Codex session transcripts to a NAS. "
    "The tool never deletes from the NAS."
)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for agent-session-backup."""
    parser = argparse.ArgumentParser(prog="agent-session-backup", description=DESCRIPTION)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    init_parser = commands.add_parser("init", help="create the destination and its sentinel")
    init_parser.add_argument(
        "--allow-local",
        action="store_true",
        help="allow a destination that is not on a network share",
    )
    run_parser = commands.add_parser("run", help="perform one backup pass")
    run_parser.add_argument(
        "-v", "--verbose", action="store_true", help="log every copy to backup.log"
    )
    run_parser.add_argument(
        "--strict", action="store_true", help="exit 3 when the destination is unavailable"
    )
    check_parser = commands.add_parser(
        "check", help="show what a run would copy, without writing anything"
    )
    check_parser.add_argument(
        "-v", "--verbose", action="store_true", help="list each planned copy and preserve"
    )
    status_parser = commands.add_parser("status", help="show the last run of every host")
    status_parser.add_argument(
        "--max-age",
        type=_duration,
        default=status.DEFAULT_MAX_AGE,
        help="mark a host STALE when its last success is older than this, such as 48h",
    )
    install_parser = commands.add_parser("install-schedule", help="install the hourly job")
    install_parser.add_argument(
        "--executable",
        help="absolute path of the program that the job runs (default: this program)",
    )
    install_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the result word without changing the schedule",
    )
    uninstall_parser = commands.add_parser("uninstall-schedule", help="remove the hourly job")
    uninstall_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the result word without changing the schedule",
    )
    return parser


def _report(message: str) -> None:
    print(f"agent-session-backup: {message}", file=sys.stderr)


def _run_init(args: argparse.Namespace) -> int:
    try:
        config = load()
        created = destination.init(config.dest_root, config.host, allow_local=args.allow_local)
    except ConfigError as error:
        _report(str(error))
        return 2
    except (destination.InitError, OSError) as error:
        _report(f"init: {error}")
        return 1
    sentinel = config.dest_root / destination.SENTINEL_NAME
    print(f"created {sentinel}" if created else f"already initialized: {sentinel}")
    return 0


def _run_run(args: argparse.Namespace) -> int:
    state_dir = paths.state_dir()
    runner.configure_logging(state_dir, verbose=args.verbose)
    try:
        config = load()
    except ConfigError as error:
        runner.logger.error("configuration error: %s", error)
        return 2
    return runner.run_pass(config, state_dir, strict=args.strict)


def _run_check(args: argparse.Namespace) -> int:
    try:
        config = load()
    except ConfigError as error:
        _report(str(error))
        return 2
    return runner.check_pass(config, verbose=args.verbose)


def _duration(text: str) -> timedelta:
    try:
        return status.parse_duration(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _run_status(args: argparse.Namespace) -> int:
    try:
        config = load()
    except ConfigError as error:
        _report(str(error))
        return 2
    return status.report(config.dest_root, now=runner.utc_now(), max_age=args.max_age)


def _run_install_schedule(args: argparse.Namespace) -> int:
    # Normalize like os.path.abspath, collapsing "..", without resolving symlinks.
    executable = args.executable or os.path.normpath(Path.cwd() / sys.argv[0])
    problem = schedule.validate_executable(executable)
    if problem is not None:
        _report(f"install-schedule: {problem}")
        return 2
    try:
        print(schedule.install(executable, dry_run=args.dry_run))
    except ScheduleError as error:
        _report(f"install-schedule: {error}")
        return 1
    return 0


def _run_uninstall_schedule(args: argparse.Namespace) -> int:
    try:
        print(schedule.uninstall(dry_run=args.dry_run))
    except ScheduleError as error:
        _report(f"uninstall-schedule: {error}")
        return 1
    return 0


_COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "init": _run_init,
    "run": _run_run,
    "check": _run_check,
    "status": _run_status,
    "install-schedule": _run_install_schedule,
    "uninstall-schedule": _run_uninstall_schedule,
}


def main(argv: Sequence[str] | None = None) -> int:
    """Run agent-session-backup and return its exit status."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return _COMMANDS[args.command](args)
