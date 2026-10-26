# AGENTS.md

Guidance for coding agents that work in this repository.

## Project

agent-session-backup copies Claude Code and Codex session transcripts and prompt
history from each machine to a NAS every hour. The NAS copy is the long-term
archive. Claude Code deletes local transcripts after `cleanupPeriodDays`, and
the backup never deletes anything on the NAS.

agent-session-backup is a Python package with the import name `agent_session_backup`
and a `src/` layout. It supports Python 3.11 and newer.

## Commands

Use the `just` recipes. CI calls the same recipes, so a local pass predicts a
CI pass.

| Recipe | Effect |
| --- | --- |
| `just sync` | Installs the locked environment. |
| `just lint` | Runs ruff, ruff format in check mode, actionlint, and zizmor. Read-only. |
| `just format` | Applies ruff format and the safe ruff fixes. |
| `just type-check` | Runs mypy in strict mode. |
| `just test` | Runs pytest with a 90% branch-coverage floor. |
| `just check` | Runs lint, type-check, and test. Run it before every commit. |
| `just build package-check` | Builds the package, installs the wheel in a clean environment, and smoke-tests it. |
| `just lock` | Updates `uv.lock` after a dependency change. |
| `just release <bump>` | Bumps the version, commits, and tags. Does not push. |

## Invariants

- Nothing under `dest_root` is ever deleted. `transfer.py` removes only the
  `.<name>.<pid>.tmp` files that it wrote, and a test records every unlink to
  check this.
- Add a source only by extending an allowlist in `sources.py`. No other code
  may walk the tool homes or copy a path that the allowlist does not name.
- Tests never touch the real home directory, the crontab, `launchctl`, or the
  NAS. `tests/conftest.py` points the environment at a temporary directory, and
  fake `crontab` and `launchctl` programs or injected runners stand in for the
  real ones.

## Rules

- Use uv for every Python operation. Add dependencies with `uv add` or
  `uv add --dev`, and commit `uv.lock`.
- `exclude-newer = "7 days"` in `pyproject.toml` hides releases younger than
  a week. Do not remove it to get a newer release; wait for the release to
  age.
- The version exists only in `pyproject.toml`. Change it with
  `just release`. Code reads it with `importlib.metadata.version()`.
- Workflows call `just` recipes and nothing else, apart from setup steps. To
  change what CI does, change a recipe.
- Actions are pinned to a full commit SHA with a `# vX.Y.Z` comment, every
  checkout sets `persist-credentials: false`, and each workflow starts from
  `permissions: {}`. zizmor enforces this in `just lint`.
- Dependabot does not update these pins, so bump them by hand:
  - `just-version` in every workflow.
  - The uv `version` in the CI lint job. It matches the uv in Dependabot's
    updater, so the lint job proves that Dependabot can update `uv.lock`.
    Raise it, and `required-version` in `pyproject.toml`, only after a
    Dependabot job log shows a newer uv.
- Each test-matrix leg sets `UV_PYTHON`. Without it, uv follows
  `.python-version` and every leg tests the same interpreter.
- A Python version in the test matrix is a support claim. Keep the matrix,
  the classifiers, `requires-python`, ruff's `target-version`, and mypy's
  `python_version` consistent.
- The pre-commit hook runs `just lint` and changes nothing. If it fails, run
  `just format`, then commit again.
