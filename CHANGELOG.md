# Changelog

All notable changes to cq. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow SemVer. A bump of a pinned measuring tool is listed under **Changed**: it moves the
numbers, and the trend shows a step there.

## [0.2.0] - 2026-10-17

### Added

- `cq init` offers cq's Claude Code plugin (`/code-quality`, `/setup-cq`): just you (user scope),
  this repo (project scope, written to `.claude/settings.json` for the team), or skip. It runs
  `claude plugin marketplace add` then `claude plugin install` at the chosen scope. It doesn't ask
  when the plugin is already installed for the user or for this repo, and without Claude Code it
  prints the lines to paste. `--skills user|project|none` answers without asking; `--yes` alone
  means user.
- `cq doctor` reports the plugin as an optional `claude plugin` line.
- The README opens with two quick starts: from Claude Code (`/setup-cq`) and from the terminal.

### Changed

- `/setup-cq` passes `--skills` to `cq init`, and offers the project install so the team gets the
  skills.

No measurement changed: snapshots from 0.1.0 and 0.2.0 compare directly.

## [0.1.0] - 2026-10-17

First release, extracted from angel-monitor's `code_quality/` (angel-monitor#391).

### Added

- `cq` command: `run`, `gate`, `report`, `backfill`, `complexity`, `doctor`, `setup` and `init`.
  `--repo` names the measured repo; cq never measures its own files.
- Settings in `cq.toml` or `[tool.cq]`, and targets detected when there are none: a
  `pyproject.toml` at the root is one Python app.
- Layered tool configs: a file the settings name, else the repo's own config for the tool, else
  cq's packaged default. Every snapshot records which config each tool read and its hash
  (`configs`), and the cq version (`tool_versions.cq`).
- `cq doctor` lists the measuring tools and the command for each missing one. `cq setup` installs
  them with Homebrew, and qlty from its GitHub release after checking its checksum and attestation.
- `cq init` writes starter settings (the gate in `warn` mode) and cq's default `.qlty/qlty.toml`,
  asking first.
- The report and the dashboard flag a measurement change since the previous snapshot (a tool
  version, or a config added, removed or changed) above the numbers.
- `vulture_config` and `interrogate_config` settings name one toml for every Python app.
- `CQ_CONFIG` names the settings file, like `--config`; `cq --version`.
- `name` setting: the dashboard's title (the repo folder's name by default).
- The data folder (`.cq/` by default) ignores itself.
- A Claude Code plugin in the same repo: `/code-quality` and `/setup-cq`.
- Generic house rules (`cq-no-print`, `cq-logs-and-carries-on`, `cq-nested-import`,
  `cq-naive-datetime`, `cq-ui-console-log`, `cq-test-only-asserts-mocks`).

### Fixed

- The gate silently skipped a target at the repo root (`root = "."`).
- deptry and the other tools run in an app's environment assumed a `dev` extra and failed on a
  `dev` dependency group.
- pyscn crashed when it found no clones (`"clone_pairs": null`).
- A past commit's worktree behind a symlinked temp folder (macOS) made every base finding look new
  in the gate.
- cq's own Python tools (vulture, semgrep, interrogate, …) are found in cq's environment, so they
  run when cq is installed with `uv tool install`.
- The import-linter parser only read chains starting with `app.`.
