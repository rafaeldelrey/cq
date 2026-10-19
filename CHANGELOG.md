# Changelog

All notable changes to cq. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow SemVer. A bump of a pinned measuring tool is listed under **Changed**: it moves the
numbers, and the trend shows a step there.

## [0.4.0] - 2026-10-19

### Added

- Each run writes `findings-totals.json`: per target and tool, how many findings `findings.jsonl`
  holds, and every list a collector cut to fit with its true total. Collectors keep each tool's
  first or worst items (15 to 200), so counting rows stops short; count from this file or the
  snapshot.
- A tool's result records `truncated` (list name → true total) whenever a list was cut. The report
  names the lists kept short under each app's table ("3 of 10"), and so does the dashboard's
  findings view. Old snapshots have no `truncated` and read as before.
- Complexity records `over_limit_items`, every function over the CC limit, as cognitive already did.

### Changed

- `findings.jsonl` has one complexity or cognitive finding per function over the limit. Before, it
  had at most 15, taken from each tool's worst list. A repo with more functions over a limit gets
  the rest as new findings once. No metric changed: `over_limit` already counted them all.
  Re-rendering older history can show more cognitive findings too, since cognitive's snapshots
  already stored `over_limit_items`.
- Every list a collector shortens goes through one cap. The gate keeps every item, as before.

No metric changed: snapshots from 0.3.2 and 0.4.0 compare directly.

## [0.3.2] - 2026-10-19

### Fixed

- Duplication is measured for a repo that sits under a folder its own `.gitignore` names (`tmp/`,
  `build/`, …). jscpd now gets the source paths relative to the repo; with absolute paths its
  `--gitignore` matched every file, so it measured nothing and cq failed. Numbers for a repo that
  measured fine before are unchanged.
- A repo with no docs to check gets `links: skipped` with the reason, instead of a tool error.
- When a tool writes no report (complexipy, trivy, deptry, jscpd, lychee, betterleaks, semgrep,
  vitest, pip-audit), the tool error names the tool and the file, instead of a bare
  `FileNotFoundError`.

No measurement changed: snapshots from 0.3.1 and 0.3.2 compare directly.

## [0.3.1] - 2026-10-19

### Fixed

- `cq init` writes qlty's `.qlty/.gitignore` with `.qlty/qlty.toml`, the same file `qlty init`
  writes. Before, every run of a qlty-based tool (radarlint, smells, workflow security, the gate's
  line checks) left qlty's cache under `.qlty/` as untracked files. A repo set up by an earlier cq,
  with the config but no `.gitignore`, is offered one by `cq init`, and `cq doctor` points it out.
  A repo's own `.qlty/.gitignore` is never changed.
- qlty's cache under `.qlty/` no longer makes a snapshot dirty, even in a repo without that
  `.gitignore`. Changes to the repo's own qlty config still do.

No measurement changed: snapshots from 0.3.0 and 0.3.1 compare directly.

## [0.3.0] - 2026-10-19

### Added

- `/improve-code-quality`, a third skill in cq's Claude Code plugin. It improves a repo in rounds:
  measure with cq, fix, review the fixes (Matt Pocock's `/code-review`, or Claude Code's own), fix
  what the review finds, test, commit. It stops at the rule you pick: a plateau, targets, or 10
  rounds at most. It first asks when to stop, whether config may silence false positives, how the work
  lands, and what is in scope. It follows the repo's own ship rules when `AGENTS.md` has them,
  and leaves alone the areas those rules say need evidence first. It ends with a report that
  separates what code fixed from what config hid, and, for a PR, follows CI and the review (Codex,
  or a fresh Claude review without it) until the PR is ready, then asks. It never merges.

No measurement changed: snapshots from 0.2.3 and 0.3.0 compare directly.

## [0.2.3] - 2026-10-18

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
