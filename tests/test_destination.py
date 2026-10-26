"""Tests for the destination guard and the init step."""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from agent_session_backup import __version__, destination
from agent_session_backup.destination import (
    Available,
    InitError,
    Unavailable,
    check,
    filesystem_type,
    init,
)

MOUNTINFO_AUTOFS_THEN_CIFS = "\n".join(
    [
        "36 25 0:31 / /mnt/synology rw,relatime shared:9 - autofs systemd-1 rw,fd=44",
        "37 36 0:45 / /mnt/synology rw,relatime shared:10 - cifs //nas/archive rw,vers=3.1.1",
    ]
)
MOUNTINFO_NESTED = "\n".join(
    [
        "20 1 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw",
        "21 20 0:50 / /mnt/synology/backups rw,relatime shared:2 - cifs //nas/backups rw",
        "22 20 0:51 / /mnt/my\\040share rw,relatime shared:3 - nfs4 nas:/export rw",
    ]
)
MOUNT_OUTPUT = "\n".join(
    [
        "/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)",
        "not a mount line",
        "//user@nas/share on /Volumes/share (smbfs, nodev, nosuid, mounted by agude)",
    ]
)


def test_check_reports_a_missing_directory(tmp_path: Path) -> None:
    assert check(tmp_path / "absent") == Unavailable("dest_root does not exist")


def test_check_reports_a_missing_sentinel(tmp_path: Path) -> None:
    assert check(tmp_path) == Unavailable("sentinel missing")


def test_check_accepts_a_directory_with_its_sentinel(tmp_path: Path) -> None:
    (tmp_path / destination.SENTINEL_NAME).write_text("{}\n", encoding="utf-8")
    assert check(tmp_path) == Available()


def test_check_reports_a_path_that_is_not_a_directory(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.write_text("x", encoding="utf-8")
    assert check(plain) == Unavailable("dest_root is not a directory")


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any directory")
def test_check_reports_an_unreadable_directory(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        result = check(locked)
    finally:
        locked.chmod(0o700)
    assert isinstance(result, Unavailable)
    assert result.reason.startswith("dest_root is unreadable")


def test_check_abandons_a_hung_listing_after_the_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()

    def hung_scandir(path: object) -> list[object]:
        release.wait()
        return []

    monkeypatch.setattr(os, "scandir", hung_scandir)
    started = time.monotonic()
    try:
        result = check(tmp_path, timeout=0.1)
    finally:
        release.set()
    assert result == Unavailable("dest_root timed out")
    assert time.monotonic() - started < 1


def test_filesystem_type_uses_the_last_entry_at_a_shared_mount_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(MOUNTINFO_AUTOFS_THEN_CIFS + "\n", encoding="utf-8")
    monkeypatch.setattr(destination, "MOUNTINFO_PATH", mountinfo)
    monkeypatch.setattr(sys, "platform", "linux")
    assert filesystem_type(Path("/mnt/synology")) == "cifs"


def test_filesystem_type_prefers_the_longest_mount_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(MOUNTINFO_NESTED + "\n", encoding="utf-8")
    monkeypatch.setattr(destination, "MOUNTINFO_PATH", mountinfo)
    monkeypatch.setattr(sys, "platform", "linux")
    assert filesystem_type(Path("/mnt/synology/backups/agent-sessions")) == "cifs"
    assert filesystem_type(Path("/home/agude")) == "ext4"
    assert filesystem_type(Path("/mnt/my share/archive")) == "nfs4"


def test_filesystem_type_parses_macos_mount_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, stdout=MOUNT_OUTPUT, stderr=""
        ),
    )
    assert filesystem_type(Path("/Volumes/share/archive")) == "smbfs"
    assert filesystem_type(Path("/home/agude")) == "apfs"


def test_filesystem_type_raises_when_the_mount_command_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, stdout="", stderr="boom\n"),
    )
    with pytest.raises(OSError, match="boom"):
        filesystem_type(Path("/Volumes/share"))


def test_init_refuses_a_local_filesystem_and_creates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(destination, "filesystem_type", lambda path: "ext4")
    target = tmp_path / "archive"
    with pytest.raises(InitError, match="not a network share"):
        init(target, "dirac")
    assert not target.exists()


def test_init_accepts_a_network_share(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(destination, "filesystem_type", lambda path: "cifs")
    assert init(tmp_path / "archive", "dirac") is True


def test_init_writes_the_sentinel_once_and_leaves_it_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(destination, "filesystem_type", lambda path: "ext4")
    target = tmp_path / "archive"
    assert init(target, "dirac", allow_local=True) is True
    sentinel = target / destination.SENTINEL_NAME
    record = json.loads(sentinel.read_text(encoding="utf-8"))
    assert set(record) == {"created", "host", "version"}
    assert record["host"] == "dirac"
    assert record["version"] == __version__
    first = sentinel.read_bytes()
    assert init(target, "dirac", allow_local=True) is False
    assert sentinel.read_bytes() == first
