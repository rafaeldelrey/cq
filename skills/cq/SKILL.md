---
name: cq
description: Measure a repository's code quality with the cq CLI (coverage, pyright, ruff, cyclomatic and cognitive complexity, maintainability, dead code, duplication, docstrings, semgrep house rules, import contracts, dependency hygiene, vulnerabilities, secrets and live keys, mutation testing, hotspots, suppressions, mock-only tests, Dockerfile and image scans, dependency freshness, shell and workflow linting, doc links, and the AI-slop tools radarlint, smells, aislop, pyscn, fallow, vale and cspell), store the snapshot, and report how quality has moved over time, with a Markdown report, an interactive dashboard and a written analysis. Use when the user types /cq [run|report|backfill|gate], or asks about code quality, its trend, a quality report, or what a branch adds.
argument-hint: "[run|report|backfill <since> [every-days]|gate [base]]  (default: run)"
---

# /cq: snapshots, trend, report

**Mode:** `$ARGUMENTS` (empty means `run`).

Everything runs through the `cq` command, from the repo's root. If `cq` is not on the PATH, or the
repo has never been set up, run `/setup-cq` first.

| What | Where |
|---|---|
| Settings | `cq.toml` at the repo root, or `[tool.cq]` in `pyproject.toml`, or nothing (targets detected). Each tool reads the repo's own config first (`[tool.vulture]`, `cspell.json`, `.vale.ini`, `.gitleaks.toml`, `.semgrep/`), then cq's default |
| Trend | `<data_dir>/history/<commit-date>_<sha>.json`: one per measured commit (`data_dir` defaults to `.cq/`) |
| A run | `<data_dir>/runs/<UTC timestamp>_<sha>/`: `snapshot.json`, `run.log`, `report.md`, `dashboard.html`, `findings.jsonl`, `findings-totals.json`, `analysis.md`, and per target each tool's raw output plus `<tool>.log` |
| Git | the data folder ignores itself: nothing a run produces is committed. `cq backfill` rebuilds lost history |

## `run` (default)

1. **Measure.** `cq run` (a minute or two). It writes the history file, the run folder, `report.md` and `dashboard.html`, and prints the run folder. A tool that fails is recorded as an error in the snapshot, with its full output in `runs/<run>/<target>/<tool>.log`. Read that log before saying why it failed; `cq doctor` says whether a tool is missing.
2. **Read** `runs/<run>/report.md` and `runs/<run>/run.log`. `runs/<run>/findings.jsonl` has every finding in one shape (tool, rule, severity, file, line, message, `new`); filter it with `jq` instead of re-reading tool output, e.g. `jq -c 'select(.new==true and .severity!="info")' findings.jsonl`. Its rows stop short where a tool's list was cut: count from `runs/<run>/findings-totals.json` (each cut list's true total) or the snapshot's numbers, never from rows.
3. **Find the cause of every regression.** For each metric that got worse since the previous snapshot:
   - take the two commits from the history files (`short` of the previous snapshot, and this one);
   - list what landed in between on that target: `git log --oneline <prev>..<now> -- <target root>`;
   - match the report's "New since the previous snapshot" offenders (functions over a limit, new clones, new dead code) to the commits that added them: `git log -L` or `git log -S<name>`.

   Name the commit and the PR or issue, never just the number.
4. **Write `runs/<run>/analysis.md`.** It is shown at the top of the dashboard. Keep to this shape, short and specific:
   ```markdown
   ## Verdict
   One or two sentences: better, worse or flat since the previous snapshot and over 30 days, and the one thing that matters most.

   ## Worse
   - **<target> <metric>** <before> → <after>: <cause, commit/PR>. <what would fix it>.

   ## Better
   - **<target> <metric>** <before> → <after>: <cause>.

   ## Next
   1. <the highest-value fix, with the file or function>
   2. …  (three at most, ranked by value per effort)
   ```
   - Size every change ("+3 functions over the CC limit", "−0.4 points of coverage"). Small deltas matter: don't call them noise unless the history shows that metric moves that much from run to run.
   - Dead code at 60% confidence is a candidate list, not a finding: check one before calling it dead (web routes, model fields and framework hooks are called by name).
   - A regression that would fail a gate the repo runs (a coverage floor, the CC limit) goes first.
   - Security comes before everything else: a secret in the current tree, a critical or high vulnerability, a broken import contract. Name it in the Verdict.
   - Secrets in git history: never print or look up the value (nothing is stored but kind, file, line and commit). trufflehog's `live-*` findings are keys their provider still accepts: name them first.
   - The AI-slop tools (`radarlint`, `smells`, `aislop`, `pyscn`, `fallow`, `vale`, `cspell`): a new finding in code written since the last run is worth naming with the commit that added it.
   - A step in a metric can come from a config change rather than the code: compare the snapshots' `configs` hashes and `tool_versions` before blaming a commit.
5. **Render the dashboard again** with the analysis: `cq report --run <run>`.
6. **Reply** with the verdict, the worse and better lists in one line each, and the paths of `dashboard.html` and the run folder. Offer to publish the dashboard as a page if the session can; the page embeds its own data, so it needs no other files. Don't paste the whole report.

## `report`

Re-render the latest run without measuring: `cq report` (or `--run <run>`), then do steps 2 to 6 of `run` if there is no `analysis.md` yet.

## `backfill <since> [every-days]`

Fill the trend with past commits: `cq backfill --since <YYYY-MM-DD> --every <days>` (default 14). Run `--dry-run` first and say how many commits it will measure (about a minute each). Each commit is measured in a temporary worktree with today's tools and config. Run it in the background, then re-render the latest run's report so the trend includes the new points.

## `gate [base]`

What the current branch adds over its base: `cq gate --base <base>` (default `origin/main`). It prints a Markdown summary: the blocking findings, the other new ones, and the tools that didn't run. List the blocking findings with file and line, and say which ones to fix and which deserve a reason in the PR instead. In `warn` mode (the default) it never fails; `block` mode is set in the settings' `gate` table.

## Rules

- Don't change the settings, a tool's config or its ignore lists during a run. Propose the change, with what it would hide or reveal, and let the user decide.
- Don't fix the code the report points at during a `/cq` run. Offer the fixes in "Next".
- cq pins its measuring tools per release, so a cq upgrade can move the numbers: say so when `tool_versions` changed between two snapshots.
