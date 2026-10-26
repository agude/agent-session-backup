"""Load the configuration file and apply the platform defaults."""

import os
import re
import socket
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agent_session_backup import paths

HOST_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_TOP_LEVEL_KEYS = frozenset({"host", "dest_root", "claude_home", "codex_home", "sources"})
_SOURCE_KEYS = frozenset({"claude", "codex"})


class ConfigError(Exception):
    """The configuration file is unreadable, invalid, or incomplete."""

    def __init__(self, path: Path, message: str) -> None:
        super().__init__(f"{path}: {message}")


@dataclass(frozen=True)
class Sources:
    """Which tools the backup reads."""

    claude: bool = True
    codex: bool = True


@dataclass(frozen=True)
class Config:
    """The effective configuration, with every default applied."""

    host: str
    dest_root: Path
    claude_home: Path
    codex_home: Path
    sources: Sources


def load(path: Path | None = None) -> Config:
    """Load the configuration file and fill every missing key with its default.

    With no argument, the file is ``paths.config_file()``. A missing file yields
    the defaults, except that macOS requires ``dest_root``.
    """
    config_path = paths.config_file() if path is None else path
    data = _read(config_path)
    _reject_unknown(config_path, data, _TOP_LEVEL_KEYS, prefix="")
    sources = _table(config_path, data, "sources")
    _reject_unknown(config_path, sources, _SOURCE_KEYS, prefix="sources.")
    return Config(
        host=_host(config_path, data),
        dest_root=_dest_root(config_path, data),
        claude_home=_path(config_path, data, "claude_home", Path.home() / ".claude"),
        codex_home=_path(config_path, data, "codex_home", _codex_default()),
        sources=Sources(
            claude=_flag(config_path, sources, "claude"),
            codex=_flag(config_path, sources, "codex"),
        ),
    )


def _read(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise ConfigError(path, f"cannot read the file: {error}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(path, f"invalid TOML: {error}") from error


def _reject_unknown(
    path: Path, table: Mapping[str, object], known: frozenset[str], prefix: str
) -> None:
    unknown = sorted(set(table) - known)
    if unknown:
        keys = ", ".join(prefix + key for key in unknown)
        raise ConfigError(path, f"unknown key {keys}")


def _table(path: Path, data: Mapping[str, object], key: str) -> Mapping[str, object]:
    if key not in data:
        return {}
    value = data[key]
    if not isinstance(value, dict):
        raise ConfigError(path, f"{key} must be a table")
    return value


def _string(path: Path, data: Mapping[str, object], key: str) -> str:
    value = data[key]
    if not isinstance(value, str):
        raise ConfigError(path, f"{key} must be a string")
    return value


def _flag(path: Path, sources: Mapping[str, object], key: str) -> bool:
    value = sources.get(key, True)
    if not isinstance(value, bool):
        raise ConfigError(path, f"sources.{key} must be true or false")
    return value


def _host(path: Path, data: Mapping[str, object]) -> str:
    if "host" in data:
        host = _string(path, data, "host")
    else:
        host = socket.gethostname().split(".")[0].lower()
    if HOST_PATTERN.fullmatch(host) is None:
        raise ConfigError(path, f"host must match ^[a-z0-9][a-z0-9-]*$, got {host!r}")
    return host


def _dest_root(path: Path, data: Mapping[str, object]) -> Path:
    if "dest_root" in data:
        return Path(_string(path, data, "dest_root")).expanduser()
    if paths.is_macos():
        raise ConfigError(path, "dest_root is required on macOS")
    return Path.home() / "synology" / "Archive" / "agent-sessions"


def _path(path: Path, data: Mapping[str, object], key: str, default: Path) -> Path:
    if key not in data:
        return default
    return Path(_string(path, data, key)).expanduser()


def _codex_default() -> Path:
    override = os.environ.get("CODEX_HOME", "")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".codex"
