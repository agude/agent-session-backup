"""Shared fixtures."""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every path the package reads at a temporary directory.

    No test then reads or writes the real home directory, configuration, state,
    or crontab.
    """
    home = tmp_path / "isolated-home"
    monkeypatch.setenv("HOME", str(home))
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


@pytest.fixture
def fake_crontab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeCrontab:
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
