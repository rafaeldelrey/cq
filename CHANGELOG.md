# Changelog

All notable changes to cq. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow SemVer. A bump of a pinned measuring tool is listed under **Changed**: it moves the
numbers, and the trend shows a step there.

## [0.2.3] - 2026-10-19

### Fixed

- The README and `/setup-cq` installed cq 0.2.0, two releases behind (and without 0.2.1's mcp fix):
  their install lines now name the current release. A test keeps every pinned install line equal to
  the package's version, so a release can't leave them behind again.

No measurement changed: snapshots from 0.2.2 and 0.2.3 compare directly.

## [0.2.2] - 2026-10-18

cq measured on itself went from 348 findings to 19 (121 warnings to 2), over five rounds of
measuring, fixing and reviewing (#5).

### Fixed

- A collector that returns no result is recorded as a tool error with a message. Before, the error
  was empty, and rendering the report failed.

### Changed

- cq's code is reorganized, with no change to any metric or any finding's key. The keys are
  checked against 96 stored snapshots (angel-monitor's history included), and a committed fixture
  now covers every tool.
  - `findings` is split into `finding` (a finding's shape and key), `extractors` (one per tool) and
    `findings` (a snapshot's list).
  - `gate` is split into `diff` (what git says changed), `compare` (head against base) and `gate`.
  - No function is over cyclomatic or cognitive complexity 15, and the complexity baseline is empty.
  - Every function has a docstring. Near-duplicate code went from 5.9% to 0.
- cq's own repo has its own tool config: `cspell.json`, `[tool.deptry]`, `.semgrep/` (cq's rules,
  without the print rule for the CLI's modules), `[tool.cq]` and `.qlty/qlty.toml`.

No measurement changed: snapshots from 0.2.1 and 0.2.2 compare directly.

## [0.2.1] - 2026-10-17

### Fixed

- cq's lock pins mcp 1.30.0 (an override of semgrep's `mcp==1.29.0`), which closes CVE-2026-59951
  in the development environment and CI. semgrep's version is unchanged. `uv tool install` doesn't
  apply a package's overrides, so an installed cq still has mcp 1.29.0. Only `semgrep mcp`, semgrep's
  MCP server, loads it, and cq never runs that.

### Changed

- When trivy, lychee, trufflehog or qlty write output without a field they always write (trivy's
  `Metadata`, lychee's `error_map`, trufflehog's git source, a SARIF result's location or message),
  the tool is reported as an error. Before, it counted as a clean result: no vulnerabilities, no
  broken links, a blank file.
- cq's own code is clean under aislop: the CLI's prints are marked as intentional, and `cq`
  dispatches its commands from a table.

No measurement changed: snapshots from 0.2.0 and 0.2.1 compare directly.

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
