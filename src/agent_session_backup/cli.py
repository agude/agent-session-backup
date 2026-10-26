"""Command-line interface for agent-session-backup."""

import argparse
from collections.abc import Sequence

from agent_session_backup import __version__


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for agent-session-backup."""
    parser = argparse.ArgumentParser(
        prog="agent-session-backup",
        description=(
            "Hourly archive of Claude Code and Codex session transcripts to a NAS. "
            "The tool never deletes from the NAS."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run agent-session-backup and return its exit status."""
    build_parser().parse_args(argv)
    return 0
