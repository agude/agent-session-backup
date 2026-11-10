"""Shared fixtures."""

import logging
import os
import subprocess
import sys
from collections.abc import Generator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

# The platform the suite runs on. Tests patch sys.platform to exercise other platforms.
ON_WINDOWS = sys.platform == "win32"
# Marks a test that runs a sh script by name, which Windows cannot do.
RUNS_SH_SCRIPTS = pytest.mark.skipif(ON_WINDOWS, reason="Windows cannot run a sh script by name")


def _reads_cannot_be_denied() -> bool:
    """Whether chmod cannot make a file unreadable: as root, or on Windows."""
    if sys.platform == "win32":
        return True
    return os.geteuid() == 0


# Marks a test that makes a path unreadable with chmod.
READ_DENIAL = pytest.mark.skipif(
    _reads_cannot_be_denied(), reason="chmod cannot deny reads as root or on Windows"
)
# Windows refuses to create a symbolic link without Developer Mode or administrator
# rights, and reports ERROR_PRIVILEGE_NOT_HELD.
PRIVILEGE_NOT_HELD = 1314


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, None, None]:
    """Skip a test that cannot create the symbolic link it needs on this Windows machine."""
    try:
        return (yield)
    except OSError as error:
        if getattr(error, "winerror", None) == PRIVILEGE_NOT_HELD:
            pytest.skip("creating a symbolic link needs Developer Mode on Windows")
        raise


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every path the package reads at a temporary directory.

    No test then reads or writes the real home directory, configuration, state,
    or crontab.
    """
    home = tmp_path / "isolated-home"
    monkeypatch.setenv("HOME", str(home))
    # Path.home() reads USERPROFILE on Windows, and the app directories come from these.
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    monkeypatch.setenv("AGENT_SESSION_BACKUP_CONFIG", str(home / "config.toml"))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))


@pytest.fixture(autouse=True)
def reset_package_logging() -> Iterator[None]:
    """Detach the package's log handlers after each test."""
    yield
    logger = logging.getLogger("agent_session_backup")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


FAKE_CRONTAB = """#!/bin/sh
# Test double for crontab. Its state is in $FAKE_CRONTAB_STATE, and each call is logged
# to $FAKE_CRONTAB_LOG.
echo "$*" >> "$FAKE_CRONTAB_LOG"
case "$1" in
  -l)
    if [ -f "$FAKE_CRONTAB_STATE" ]; then
      exec /bin/cat "$FAKE_CRONTAB_STATE"
    fi
    echo "no crontab for tester" >&2
    exit 1
    ;;
  -)
    exec /bin/cat > "$FAKE_CRONTAB_STATE"
    ;;
  *)
    echo "unsupported arguments: $*" >&2
    exit 2
    ;;
esac
"""


@dataclass(frozen=True)
class FakeCrontab:
    """A crontab stand-in on PATH, with its state and call log in the temporary directory."""

    state: Path
    log: Path

    def text(self) -> str:
        return self.state.read_text(encoding="utf-8")

    def write(self, text: str) -> None:
        self.state.write_text(text, encoding="utf-8")

    def writes(self) -> int:
        """Return how many times the crontab was written."""
        if not self.log.exists():
            return 0
        return sum(1 for line in self.log.read_text(encoding="utf-8").splitlines() if line == "-")


def _emulate_crontab(fake: FakeCrontab, monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer crontab calls in this process, as FAKE_CRONTAB does, for a platform without sh.

    Only calls whose program is ``crontab`` are answered; every other call runs.
    """
    real_run = subprocess.run

    def run(args: Any, *positional: Any, **keywords: Any) -> Any:
        if not isinstance(args, list) or not args or args[0] != "crontab":
            return real_run(args, *positional, **keywords)
        with fake.log.open("a", encoding="utf-8", newline="") as log:
            log.write(" ".join(args[1:]) + "\n")
        if args[1:] == ["-l"]:
            if fake.state.exists():
                return subprocess.CompletedProcess(args, 0, fake.state.read_bytes(), b"")
            return subprocess.CompletedProcess(args, 1, b"", b"no crontab for tester\n")
        if args[1:] == ["-"]:
            fake.state.write_bytes(keywords.get("input") or b"")
            return subprocess.CompletedProcess(args, 0, b"", b"")
        unsupported = f"unsupported arguments: {args[1:]}\n".encode()
        return subprocess.CompletedProcess(args, 2, b"", unsupported)

    monkeypatch.setattr(subprocess, "run", run)


@pytest.fixture
def fake_crontab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeCrontab:
    if ON_WINDOWS:
        fake = FakeCrontab(state=tmp_path / "crontab.state", log=tmp_path / "crontab.log")
        _emulate_crontab(fake, monkeypatch)
        return fake
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    script = bin_dir / "crontab"
    script.write_text(FAKE_CRONTAB, encoding="utf-8")
    script.chmod(0o755)
    fake = FakeCrontab(state=tmp_path / "crontab.state", log=tmp_path / "crontab.log")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("FAKE_CRONTAB_STATE", str(fake.state))
    monkeypatch.setenv("FAKE_CRONTAB_LOG", str(fake.log))
    return fake
