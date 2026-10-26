"""Guard the destination: check that the NAS is mounted, and create it on first use."""

import contextlib
import json
import os
import re
import subprocess
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agent_session_backup import __version__, paths

SENTINEL_NAME = ".agent-session-backup-root"
NETWORK_FILESYSTEMS = frozenset({"cifs", "smb3", "smbfs", "nfs", "nfs4"})
TIMEOUT_SECONDS = 30.0
MOUNTINFO_PATH = Path("/proc/self/mountinfo")
MOUNT_COMMAND = "/sbin/mount"
_MOUNT_LINE = re.compile(r"^.+? on (?P<point>.+) \((?P<type>[^,)]+)")
_OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")


class InitError(Exception):
    """``init`` refused to create the destination."""


@dataclass(frozen=True)
class Available:
    """The destination can be listed and holds its sentinel."""


@dataclass(frozen=True)
class Unavailable:
    """The destination cannot be used. ``reason`` says why."""

    reason: str


Status = Available | Unavailable


def check(dest_root: Path, timeout: float = TIMEOUT_SECONDS) -> Status:
    """Return the destination status.

    The listing runs in a daemon thread, so a hung SMB server cannot hang the
    caller. A listing still running after ``timeout`` seconds is abandoned.
    """
    results: list[Status] = []
    worker = threading.Thread(target=lambda: results.append(_probe(dest_root)), daemon=True)
    worker.start()
    worker.join(timeout)
    if not results:
        return Unavailable("dest_root timed out")
    return results[0]


def _probe(dest_root: Path) -> Status:
    try:
        list(os.scandir(dest_root))  # Listing triggers the systemd automount.
    except FileNotFoundError:
        return Unavailable("dest_root does not exist")
    except NotADirectoryError:
        return Unavailable("dest_root is not a directory")
    except OSError as error:
        return Unavailable(f"dest_root is unreadable: {error}")
    if not (dest_root / SENTINEL_NAME).is_file():
        return Unavailable("sentinel missing")
    return Available()


def init(dest_root: Path, host: str, *, allow_local: bool = False) -> bool:
    """Create the destination and write its sentinel. Return whether the sentinel was written.

    The destination must be on a network filesystem unless ``allow_local`` is set.
    An existing sentinel is left unchanged.
    """
    _trigger_automount(dest_root)
    filesystem = filesystem_type(dest_root)
    if filesystem not in NETWORK_FILESYSTEMS and not allow_local:
        raise InitError(
            f"{dest_root} is on {filesystem}, which is not a network share; "
            "pass --allow-local to use it anyway"
        )
    dest_root.mkdir(parents=True, exist_ok=True)
    sentinel = dest_root / SENTINEL_NAME
    if sentinel.exists():
        return False
    record = {
        "created": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "host": host,
        "version": __version__,
    }
    sentinel.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return True


def _trigger_automount(dest_root: Path) -> None:
    """List the destination path, so that an automount on it mounts the share."""
    with contextlib.suppress(OSError):
        list(os.scandir(dest_root))


def filesystem_type(path: Path) -> str:
    """Return the filesystem type of the mount that holds ``path``."""
    if paths.is_macos():
        entries = parse_mount_output(_mount_command_text())
    else:
        entries = parse_mountinfo(_mountinfo_text())
    return _owning_filesystem(path.resolve(), entries)


def parse_mountinfo(text: str) -> list[tuple[str, str]]:
    """Return (mount point, filesystem type) for each line of ``/proc/self/mountinfo``.

    The filesystem type follows the ``-`` separator. Octal escapes in the mount
    point are decoded.
    """
    entries: list[tuple[str, str]] = []
    for line in text.splitlines():
        fields = line.split(" ")
        separator = fields.index("-")
        entries.append((_unescape(fields[4]), fields[separator + 1]))
    return entries


def parse_mount_output(text: str) -> list[tuple[str, str]]:
    """Return (mount point, filesystem type) for each ``... on <point> (<type>, ...)`` line."""
    entries: list[tuple[str, str]] = []
    for line in text.splitlines():
        match = _MOUNT_LINE.match(line)
        if match is not None:
            entries.append((match["point"], match["type"]))
    return entries


def _owning_filesystem(path: Path, entries: Sequence[tuple[str, str]]) -> str:
    """Return the type of the longest mount point that contains ``path``.

    On a tie, the later entry wins, so the last mount on a shared point takes effect.
    """
    filesystem = "unknown"
    depth = -1
    for point, fstype in entries:
        mount = Path(point)
        if path.is_relative_to(mount) and len(mount.parts) >= depth:
            depth = len(mount.parts)
            filesystem = fstype
    return filesystem


def _unescape(field: str) -> str:
    return _OCTAL_ESCAPE.sub(lambda match: chr(int(match[1], 8)), field)


def _mountinfo_text() -> str:
    return MOUNTINFO_PATH.read_text(encoding="utf-8")


def _mount_command_text() -> str:
    completed = subprocess.run([MOUNT_COMMAND], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise OSError(f"{MOUNT_COMMAND} failed: {completed.stderr.strip()}")
    return completed.stdout
