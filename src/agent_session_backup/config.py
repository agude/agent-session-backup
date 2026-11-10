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
_TOP_LEVEL_KEYS = frozenset(
    {"host", "dest_root", "claude_home", "codex_home", "claude_desktop_home", "sources"}
)
_SOURCE_KEYS = frozenset({"claude", "codex", "claude_desktop", "codex_sqlite"})
# The Claude desktop app from the Microsoft Store keeps its data in its package's
# redirected AppData; a direct install uses %APPDATA%\Claude.
CLAUDE_STORE_PACKAGE = "AnthropicPBC.Claude_*"


class ConfigError(Exception):
    """The configuration file is unreadable, invalid, or incomplete."""

    def __init__(self, path: Path, message: str) -> None:
        super().__init__(f"{path}: {message}")


@dataclass(frozen=True)
class Sources:
    """Which tools the backup reads.

    ``claude_desktop`` is the Claude desktop app's session metadata.
    ``codex_sqlite`` is snapshots of the Codex thread databases, where the Codex
    desktop app keeps conversations whose rollout files are gone.
    """

    claude: bool = True
    codex: bool = True
    claude_desktop: bool = True
    codex_sqlite: bool = False


@dataclass(frozen=True)
class Config:
    """The effective configuration, with every default applied.

    ``claude_desktop_home`` is ``None`` when no Claude desktop app data exists.
    """

    host: str
    dest_root: Path
    claude_home: Path
    codex_home: Path
    sources: Sources
    claude_desktop_home: Path | None = None


def load(path: Path | None = None) -> Config:
    """Load the configuration file and fill every missing key with its default.

    With no argument, the file is ``paths.config_file()``. A missing file yields
    the defaults, except that macOS and Windows require ``dest_root``.
    ``sources.codex_sqlite`` defaults to true on Windows only.
    """
    config_path = paths.config_file() if path is None else path
    data = _read(config_path)
    _reject_unknown(config_path, data, _TOP_LEVEL_KEYS, prefix="")
    sources = _table(config_path, data, "sources")
    _reject_unknown(config_path, sources, _SOURCE_KEYS, prefix="sources.")
    desktop_default = _claude_desktop_default()
    desktop_home = (
        _path(config_path, data, "claude_desktop_home", desktop_default)
        if desktop_default is not None
        else _optional_path(config_path, data, "claude_desktop_home")
    )
    return Config(
        host=_host(config_path, data),
        dest_root=_dest_root(config_path, data),
        claude_home=_path(config_path, data, "claude_home", Path.home() / ".claude"),
        codex_home=_path(config_path, data, "codex_home", _codex_default()),
        sources=Sources(
            claude=_flag(config_path, sources, "claude"),
            codex=_flag(config_path, sources, "codex"),
            claude_desktop=_flag(config_path, sources, "claude_desktop"),
            codex_sqlite=_flag(config_path, sources, "codex_sqlite", paths.is_windows()),
        ),
        claude_desktop_home=desktop_home,
    )


def _read(path: Path) -> dict[str, object]:
    """Parse the file as UTF-8 TOML. A leading byte-order mark is dropped.

    Windows PowerShell 5.1 writes UTF-8 with a byte-order mark, and its ``>``
    and ``Out-File`` write UTF-16, which is reported as an encoding error.
    """
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise ConfigError(path, f"cannot read the file: {error}") from error
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ConfigError(path, f"the file is not UTF-8: {error}") from error
    try:
        return tomllib.loads(text)
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


def _flag(path: Path, sources: Mapping[str, object], key: str, default: bool = True) -> bool:
    value = sources.get(key, default)
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
    if paths.is_windows():
        raise ConfigError(path, "dest_root is required on Windows")
    return Path.home() / "synology" / "Archive" / "agent-sessions"


def _path(path: Path, data: Mapping[str, object], key: str, default: Path) -> Path:
    if key not in data:
        return default
    return Path(_string(path, data, key)).expanduser()


def _optional_path(path: Path, data: Mapping[str, object], key: str) -> Path | None:
    if key not in data:
        return None
    return Path(_string(path, data, key)).expanduser()


def _claude_desktop_default() -> Path | None:
    """Return the Claude desktop app's data directory, or ``None`` where none exists.

    Only Windows is located automatically. The Store app keeps its data in its
    package's redirected AppData; a direct install keeps it in ``%APPDATA%\\Claude``.
    """
    if not paths.is_windows():
        return None
    packages = paths.local_app_data() / "Packages"
    candidates = sorted(packages.glob(CLAUDE_STORE_PACKAGE)) if packages.is_dir() else []
    for package in candidates:
        data = package / "LocalCache" / "Roaming" / "Claude"
        if data.is_dir():
            return data
    roaming = os.environ.get("APPDATA", "")
    direct = Path(roaming) / "Claude" if roaming else None
    return direct if direct is not None and direct.is_dir() else None


def _codex_default() -> Path:
    override = os.environ.get("CODEX_HOME", "")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".codex"
