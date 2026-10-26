"""Command-line interface for agent-session-backup."""

import argparse
import sys
from collections.abc import Callable, Sequence

from agent_session_backup import __version__, destination
from agent_session_backup.config import ConfigError, load

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


_COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {"init": _run_init}


def main(argv: Sequence[str] | None = None) -> int:
    """Run agent-session-backup and return its exit status."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return _COMMANDS[args.command](args)
