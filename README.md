# agent-session-backup

agent-session-backup copies Claude Code and Codex session transcripts and prompt
history from each machine to a NAS every hour. The NAS copy is the long-term
archive. Claude Code deletes local transcripts after `cleanupPeriodDays`, and
the backup never deletes anything on the NAS.

## Install

```sh
uv tool install git+https://github.com/agude/agent-session-backup@vX.Y.Z
```

Replace `vX.Y.Z` with a release tag. The repository must be public for this URL
to work without credentials. Otherwise, use an SSH URL.

## Set up

1. Mount the NAS share. On macOS, add the share to Login Items so that it mounts
   at login.
2. Write the configuration file. See [Configuration](#configuration).
3. Create the destination once:

   ```sh
   agent-session-backup init
   ```

   `init` refuses a destination that is not on a network share, so an unmounted
   share cannot be replaced by a local directory. Use `--allow-local` only for
   testing.
4. Show what a run would copy, without writing anything:

   ```sh
   agent-session-backup check
   ```
5. Run one pass and watch it:

   ```sh
   agent-session-backup run -v
   ```
6. Install the hourly job:

   ```sh
   agent-session-backup install-schedule
   ```

   On Linux, this adds a block to the crontab. On macOS, it installs a
   LaunchAgent.

## Configuration

The configuration file is `$XDG_CONFIG_HOME/agent-session-backup/config.toml`.
Without `XDG_CONFIG_HOME`, the file is `~/.config/agent-session-backup/config.toml`,
on Linux and on macOS. `AGENT_SESSION_BACKUP_CONFIG` replaces the path. Unknown
keys are errors.

| Key | Default |
| --- | --- |
| `host` | The short hostname, lowercased. Letters, digits, and hyphens only. |
| `dest_root` | Linux: `~/synology/Archive/agent-sessions`. macOS: required. |
| `claude_home` | `~/.claude` |
| `codex_home` | `$CODEX_HOME`, or `~/.codex` |
| `[sources] claude` | `true` |
| `[sources] codex` | `true` |

A macOS configuration must set `dest_root`:

```toml
dest_root = "/Volumes/<share>/Archive/agent-sessions"
```

## Commands

```
agent-session-backup init [--allow-local]
agent-session-backup run [-v] [--strict]
agent-session-backup check [-v]
agent-session-backup status [--max-age MAX_AGE]
agent-session-backup install-schedule [--executable EXECUTABLE] [--dry-run]
agent-session-backup uninstall-schedule [--dry-run]
```

- `init` creates `dest_root` and its sentinel file. An existing sentinel is left
  unchanged.
- `run` makes one backup pass. The hourly job calls it. `-v` logs one line per
  copy. `--strict` makes an unavailable destination exit 3 instead of 0.
- `check` shows the configuration, the destination status, and the number of
  files in each action. `-v` lists each planned copy and preserve. It takes no
  lock and writes nothing.
- `status` prints one row per host from its `.last-run.json`. A host is STALE
  when its last success is older than `--max-age`. The default is `48h`. The
  value is a count with a unit: `s`, `m`, `h`, or `d`, such as `72h`.
- `install-schedule` installs or updates the hourly job. `--executable` defaults
  to this program's path. The path must be absolute, executable, outside a
  virtual environment, and outside the system temporary directory. Run it from
  the installed tool, not with `uv run` or `uvx`. The command prints one result
  word: `installed`, `updated`, or `unchanged`.
- `uninstall-schedule` removes the hourly job and prints `removed` or `absent`.
- `--dry-run` on either schedule command prints the word that the command
  would print and changes nothing. Ansible uses it in check mode.

Exit codes:

- `init`: 0 on success. 1 when the destination is refused or cannot be created.
  2 for a configuration error.
- `run`: 0 when the pass completes, when the destination is unavailable, or when
  another run holds the lock. 1 when any file fails. 2 for a configuration error.
  3 for an unavailable destination with `--strict`.
- `check`: 0 when the destination is available. 2 for a configuration error. 3
  when the destination is unavailable.
- `status`: 0 when every host is ok and fresh. 1 when a host is stale, partial, or
  unreadable, or when no host has a record. 2 when the destination is unavailable.

Logs go to `backup.log` in the state directory. On Linux, that is
`$XDG_STATE_HOME/agent-session-backup`, which defaults to
`~/.local/state/agent-session-backup`. On macOS, it is
`~/Library/Logs/agent-session-backup`, where the LaunchAgent also writes
`launchd.log`. The state directory also holds `run.lock`, which prevents
overlapping runs.

## What is backed up

Claude Code (`~/.claude`):

- `projects/**/*.jsonl`
- `projects/*/memory/**/*.md`
- `history.jsonl`

Codex (`$CODEX_HOME`, or `~/.codex`):

- `sessions/**/*.jsonl`
- `archived_sessions/**/*.jsonl`
- `history.jsonl`
- `session_index.jsonl`

Nothing else is copied. The backup never copies credentials (`auth.json`,
`.credentials.json`), SQLite databases, `file-history/`, `todos/`, or
`settings.json`. It skips symbolic links, FIFOs, and sockets, and it does not
follow a symbolic link to a directory.

Each file lands at `<dest_root>/<host>/claude/<path>` or
`<dest_root>/<host>/codex/<path>`, where `<path>` is relative to the source home.
Each host also has a `.last-run.json` record.

## How copies work

| Destination | Action |
| --- | --- |
| Missing | Copy. |
| Smaller than the source | Copy. |
| Same size, mtimes within 2 s | Skip. |
| Same size, source newer by more than 2 s | Copy. |
| Larger than the source | Rename the destination to `<name>.<UTC timestamp>.bak`, then copy. |
| Same size, destination newer by more than 2 s | Rename the destination to `<name>.<UTC timestamp>.bak`, then copy. |

Each copy is written to a temporary file, flushed to disk, and renamed into
place. A reader sees either the old file or the new one.

## Status

`status` prints one row per host, with its finished time, status, copied and
error counts, age, and result:

- `ok`: the last run succeeded, and the last success is within `--max-age`.
- `STALE`: the last success is older than `--max-age`, or there is none.
- `partial`: the last run had errors.
- `UNREADABLE`: the record is not valid JSON or is missing fields.

## Restore

Copy files back to the same relative path under the home directory they came
from:

- Claude Code: copy `<dest_root>/<host>/claude/<path>` to `~/.claude/<path>`.
- Codex: copy `<dest_root>/<host>/codex/<path>` to `$CODEX_HOME/<path>`, or to
  `~/.codex/<path>` when `CODEX_HOME` is unset.

A Claude session file restored into `~/.claude/projects/<slug>/` appears in
`claude --resume`.

## Development

Requires [uv](https://docs.astral.sh/uv/) 0.13 or newer and [just](https://just.systems/).

The `.python-version` file selects Python 3.15. uv 0.13 or newer can
download its final release. The lower uv minimum in `pyproject.toml` lets
Dependabot update the lock on Python 3.11; CI tests that minimum.

```sh
just sync           # Install the locked development environment.
just hooks-install  # Install the pre-commit hook, which runs `just lint`.
just check          # Run lint, type-check, and test.
```

Run `just` to list every recipe.

## Releasing

```sh
just release patch  # Or minor or major. Runs `just check`, then commits and tags.
git push origin HEAD vX.Y.Z
```

## License

[CC0 1.0 Universal](LICENSE.md)
