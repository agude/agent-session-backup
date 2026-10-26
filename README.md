# agent-session-backup

agent-session-backup copies Claude Code and Codex session transcripts and prompt
history from each machine to a NAS every hour. The NAS copy is the long-term
archive. Claude Code deletes local transcripts after `cleanupPeriodDays`, and
the backup never deletes anything on the NAS.

## Usage

```sh
agent-session-backup --help
```

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
