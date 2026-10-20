# cq

Code-quality snapshots of a repository, kept as history and reported as trends, plus a PR gate
that judges only what a branch adds. About thirty tools, run with pinned versions so two snapshots
are always comparable:

- **Correctness and tests:** coverage, pytest results, pyright, ruff.
- **Complexity:** cyclomatic and cognitive complexity, maintainability.
- **Code health:** dead code, duplication, docstrings, house rules (semgrep), import contracts.
- **Dependencies:** unused and undeclared ones (deptry, knip), freshness, known vulnerabilities.
- **Security:** secrets in the tree and the git history, and which leaked keys still work.
- **Test strength:** mutation testing, tests that only assert mocks were called.
- **Repo hygiene:** churn × complexity hotspots, lint suppressions, Dockerfiles and images,
  shell scripts and GitHub workflows, broken doc links.
- **AI slop:** radarlint, qlty smells, aislop, pyscn, fallow, vale, cspell.

## Quick start

Two ways in; both end with the same setup.

**From Claude Code.** Claude installs cq, its tools and the repo's setup, and asks you before each step:

```
/plugin marketplace add rafaeldelrey/cq
/plugin install cq@cq
/setup-cq
```

Run `/setup-cq` in the repo you want to measure. Then use `/code-quality` for a snapshot and an
analysis.

**From the terminal:**

```bash
uv tool install git+ssh://git@github.com/rafaeldelrey/cq@v0.4.2
cq doctor                    # which measuring tools are missing
cq setup                     # installs them, asking first
cd my-repo && cq init        # settings, qlty's config, and the Claude Code plugin if you want it
cq run                       # the first snapshot; open the dashboard.html it prints
```

The steps below explain each of these.

**Contents:**
1. [Set up your machine](#1-set-up-your-machine) (once)
2. [Set up a repository](#2-set-up-a-repository) (once per repo)
3. [Use it](#3-use-it)
4. [Read the results](#4-read-the-results)
5. [Run the gate in CI](#5-run-the-gate-in-ci)
6. [Use it from Claude Code](#6-use-it-from-claude-code)
7. [Configure it](#7-configure-it)
8. [Troubleshooting](#8-troubleshooting)
9. [Upgrade or uninstall](#9-upgrade-or-uninstall)

---

## 1. Set up your machine

You do this once per machine. It works on macOS and Linux.

**What you need first:**

| | Why | Check | Install |
|---|---|---|---|
| git | everything | `git --version` | `brew install git` |
| [uv](https://docs.astral.sh/uv/) | installs cq; runs each app's own tools | `uv --version` | `brew install uv` |
| [Homebrew](https://brew.sh) | `cq setup` installs the measuring tools with it | `brew --version` | see brew.sh |
| GitHub ssh access | the cq repo is private | `ssh -T git@github.com` | add your ssh key to GitHub |
| GitHub CLI, signed in | `cq setup` checks qlty's build attestation with it | `gh auth status` | `brew install gh`, then `gh auth login` |

**Step 1: install cq.**

```bash
uv tool install git+ssh://git@github.com/rafaeldelrey/cq@v0.4.2
```

This puts the `cq` command on your PATH; check it with `cq --version`. Use the newest tag
(`git ls-remote --tags git@github.com:rafaeldelrey/cq.git`). The tag pins every measuring tool's
version, so keep it the same everywhere you want comparable numbers.

**Step 2: see what's missing.**

```bash
cq doctor
```

Each tool gets one line. `ok` means installed (qlty also has to be the version cq pins). `XX` means
missing and required. `--` means optional. Every line that isn't `ok` carries the command that
fixes it. hadolint and trivy are optional: they're needed only when a repo has a Dockerfile or a
published image.

**Step 3: install what's missing.**

```bash
cq setup
```

`cq setup` first prints what it will do, then asks before doing it:
- it runs `brew install` for the missing required tools;
- it downloads qlty at the pinned version from its GitHub release, checks the checksum and the
  build attestation, and installs it to `~/.local/bin`.

If `~/.local/bin` isn't on your PATH, setup says so. The same goes for an older qlty that comes
first on the PATH. At the end it runs `cq doctor` again, and you're done when that says
"Everything cq needs is installed."

On first use cq also fetches some tools by itself: jscpd, knip, aislop, fallow and cspell through
npx, and pyscn through uvx. qlty does the same for its plugins. The first run is therefore slower,
and it needs internet access.

---

## 2. Set up a repository

You do this once per repo, from its root. A Python repo with a `pyproject.toml` at the root works
with **no setup at all**: skip to [Use it](#3-use-it). `cq init` adds what a repo keeps for good.

```bash
cd ~/work/my-repo
cq init
```

`init` shows what cq will measure, then asks before each change:

```
Repo: /Users/you/work/my-repo
Settings: none (targets detected)
  my_repo (python, root .): src, tests/
  repo (repo, root .)
Data: /Users/you/work/my-repo/.cq (history/ and runs/, never committed)
```

1. **Settings.** It adds a starter `[tool.cq]` to `pyproject.toml`, or writes `cq.toml` when the
   repo has no pyproject. The starter holds the PR gate in `warn` mode. Targets stay detected.
2. **qlty's config.** It writes `.qlty/qlty.toml` when the repo has none, with the `.qlty/.gitignore`
   `qlty init` writes: qlty's cache stays out of git, and its config, plugin configs and hooks stay
   in. Without `qlty.toml` the qlty-based tools don't run: radarlint, smells, workflow security and
   the gate's line checks report an error saying "run `cq init`". A repo that already has the config
   but no `.gitignore` is offered one; `cq doctor` points it out.
3. **The Claude Code plugin** (the `/code-quality`, `/setup-cq` and `/improve-code-quality`
   skills). It shows the two `claude` commands for each choice, then asks:
   - **u, just you:** installed for your user, in every repo, with nothing written to this one;
   - **p, this repo:** written to the repo's `.claude/settings.json`; commit it, and everyone who
     opens the repo in Claude Code gets the skills;
   - **s, skip.**

   It doesn't ask when the plugin is already installed for you or for this repo. Without Claude
   Code on the machine, it prints the two lines to paste into Claude Code instead.

`cq init --yes` accepts all three without the questions, installing the plugin for you only.
`--skills user|project|none` picks the plugin's answer. Then commit what init changed: those files
are the repo's, not cq's.

**What counts as the app.** cq detects one Python app at the root. Its production code is the
first of these that holds Python files:
- `src/`;
- a package named like the project (`my_repo/`, a namespace package included);
- the top-level packages (folders with an `__init__.py`);
- the top-level modules.

Tests are `tests/`, when present. `.venv` is never production code. A **monorepo** (several apps,
or a TypeScript app) lists its apps instead; see [Configure it](#7-configure-it).

---

## 3. Use it

Run everything from the repo's root, or pass `--repo <path>` from anywhere.

**Take a snapshot.** Run this daily, after a merge, or before a refactor and after it:

```bash
cq run
```

It measures the working tree as it is (`HEAD` plus uncommitted changes), takes a minute or two,
and ends by printing where the report and the dashboard went. The snapshot goes into the trend
(`.cq/history/`), and a snapshot of uncommitted changes is stored apart, marked `-dirty`.

**Check a branch before you open a PR:**

```bash
cq gate --base origin/main
```

It measures your branch and its merge-base with `origin/main`, and lists only the findings the
branch **adds**: new code smells, new secrets, new functions without a docstring, new copied
blocks, a ratchet that got worse. The list is split into blocking and other findings. In the
default `warn` mode it always exits 0; see [Configure it](#7-configure-it) for `block`.

**Other commands:**

| Command | What it does |
|---|---|
| `cq run --tool ruff --tool dead_code` | runs only these tools (repeat `--tool`); a partial run stays out of the trend |
| `cq run --target api` | measures only this app |
| `cq run --rev a1b2c3d` | measures a past commit, in a temporary worktree outside the repo |
| `cq report` | re-renders the latest run's report and dashboard (`--run <id>` for another) |
| `cq backfill --since 2026-06-01 --dry-run` | lists the past commits it would measure, one every 14 days (`--every <days>`) on the checked-out branch (`--branch <name>`) |
| `cq backfill --since 2026-06-01` | measures them, so the trend starts before your first run: about a minute per commit |
| `cq complexity check src` | fails on a function over cyclomatic complexity 15 that isn't in `.complexity-baseline.json` |
| `cq complexity report src --base origin/main` | a Markdown table of the functions whose complexity changed (for a PR body) |
| `cq complexity baseline src` | rewrites the baseline: run it after a refactor shrinks a function |

`cq <command> --help` lists every option.

---

## 4. Read the results

Every run writes a folder under `.cq/runs/<UTC time>_<sha>/`:

| File | What's in it |
|---|---|
| `dashboard.html` | **Start here.** Open it in a browser (`open <path>` on macOS). It has the trend, the scorecard and every finding, and it needs no server |
| `report.md` | The same in Markdown: what got better or worse since the previous snapshot and over 30 days, and the worst offenders |
| `findings.jsonl` | Every finding in one shape (tool, rule, severity, file, line, message, `new`). Filter it with `jq`, e.g. `jq -c 'select(.new and .severity!="info")' findings.jsonl` |
| `findings-totals.json` | Per target and tool, how many findings `findings.jsonl` holds, and every list cut to fit with its true total. Collectors keep each tool's first or worst items (up to 15–200), so count from here or `snapshot.json`, not from rows |
| `snapshot.json` | Every number, the tool versions, and the config files each tool read |
| `run.log` | One line per tool, with its time or its error |
| `<target>/<tool>.log` | The exact commands a tool ran, their exit codes and output: read this when a tool errors |

`.cq/history/` holds one snapshot per commit; that's the trend. `.cq/` ignores itself, so nothing
cq writes ever shows up in `git status`.

**When numbers jump without a code change.** If a tool version or a config file changed since the
previous snapshot, the report and the dashboard say so above the numbers. Typical causes are a cq
upgrade, an edit to `[tool.ruff]`, or a bumped lockfile.

**A tool error is a blind spot, not a zero.** A tool that couldn't run is listed as an error in
the run and the report, never as a clean result.

---

## 5. Run the gate in CI

The gate gives you a summary of what each PR adds. On GitHub Actions it also writes that summary
to the job's page. The job needs:
- **the full history**, so the gate can find the merge-base;
- **cq**, installed with a token that can read the private cq repo;
- **the measuring tools.**

A starting point, which is not yet run on a work repo, so check its first summary:

```yaml
quality-gate:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
      with:
        fetch-depth: 0                     # the gate needs the merge-base
    - uses: astral-sh/setup-uv@v6
    - name: Install cq and its tools
      env:
        GH_TOKEN: ${{ github.token }}      # qlty's release download and attestation check
      run: |
        uv tool install "git+https://x-access-token:${{ secrets.CQ_READ_TOKEN }}@github.com/rafaeldelrey/cq@v0.4.2"
        eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"   # Homebrew is preinstalled, off the PATH
        cq setup --yes
        echo "$HOME/.local/bin" >> "$GITHUB_PATH"
        dirname "$(brew --prefix)/bin/vale" >> "$GITHUB_PATH"
    - name: cq gate
      run: cq gate --base "origin/${{ github.base_ref }}"
```

`CQ_READ_TOKEN` is a fine-grained token with read access to `rafaeldelrey/cq`, stored as a secret
of the repo the job runs in. Keep the gate in `warn` mode until the team trusts its findings.

---

## 6. Use it from Claude Code

This repo is also a Claude Code plugin. `cq init` offers to install it ([step 2](#2-set-up-a-repository)),
or, in Claude Code:

```
/plugin marketplace add rafaeldelrey/cq
/plugin install cq@cq
```

`cq doctor` shows whether it's installed, and for whom (`claude plugin` line, optional).

- **`/setup-cq`** walks a repo through steps 1 and 2: it installs cq, then runs `cq doctor`,
  `cq setup` and `cq init`, asking first, and takes the first snapshot.
- **`/code-quality`** runs cq and explains the result. It traces each regression to the commit
  that caused it, writes an analysis onto the dashboard, and ranks what to fix next.
  `/code-quality report`, `/code-quality backfill <since>` and `/code-quality gate` work too.
- **`/improve-code-quality`** improves the repo in rounds, on its own branch: measure with cq, fix,
  review the fixes, fix what the review finds, test, commit, until the stop rule you pick. It asks
  first: when to stop, whether it may silence false positives in config, how the work lands, and
  what's in scope. It ends with a report that separates what code fixed from what config hid, and,
  if you chose a PR, with a PR that CI and a reviewer (Codex, or a fresh Claude review) have
  already cleared. It never merges. Only you start it: type it in the repo.

To give a whole team the skills, install the plugin for the project: `cq init --skills project`,
or `claude plugin install cq@cq --scope project`. Commit the `.claude/settings.json` it writes.
Teammates need read access to `rafaeldelrey/cq`.

---

## 7. Configure it

Settings live in `cq.toml` at the repo root, or in `[tool.cq]` in `pyproject.toml`. The keys are
the same in both; in `cq.toml` they sit at the top level. `--config <file>` or `CQ_CONFIG=<file>`
(relative to the repo) points at a settings file anywhere else. Everything is optional:

```toml
[tool.cq]
name = "Acme"                    # the dashboard's title: "Acme code quality"
data_dir = ".cq"                 # where history/ and runs/ go, relative to this file
complexity_limit = 15
cognitive_limit = 15
shell_roots = ["."]              # where shellcheck looks for tracked *.sh
link_paths = ["README.md", "docs"]   # docs checked for broken links and AI prose
link_exclude = []
complexity_baseline = ".complexity-baseline.json"

# A file for a tool, when it isn't where cq looks by default (next table):
semgrep_rules = ""               # a folder: *.yml production rules, tests/*.yml test rules
contracts_dir = ""               # import-linter contracts, one <target>.ini per app
vulture_config = ""              # a toml with [tool.vulture], for every Python app
interrogate_config = ""          # a toml with [tool.interrogate], for every Python app
cspell_config = ""
vale_config = ""
gitleaks_config = ""

# One entry per app, for a monorepo or a TypeScript app; a repo-wide target is added for you.
[[tool.cq.targets]]
name = "api"
kind = "python"                  # python | typescript | repo
root = "apps/api"                # the app's folder
source = ["app"]                 # its production code, relative to root
tests = true
deptry_ignore = ["DEP002=uvicorn"]   # a deptry finding that's wrong for this app
mutation_paths = ["app/core/rules.py"]   # files mutation testing mutates (slow: pick the critical ones)
image = "me/api:latest"          # a published image Trivy scans

[tool.cq.gate]
mode = "warn"                    # "block": exit 1 on a blocking finding or a tool that didn't run
block = ["secrets:*", "semgrep:cq-*", "smells:*", "ratchet:*"]   # tool:rule patterns that block
```

### Which config each tool reads

Each tool reads the first of these that exists:

1. the file named in the settings;
2. the repo's own config for that tool;
3. cq's default (in `cq/defaults/`).

So a repo that already configures a tool gets the same answers from cq as from running that tool
by hand.

| Tool | The repo's own config |
|---|---|
| ruff, pyright, pytest, coverage | the app's `pyproject.toml`, always: they run in the app's own environment, at the app's own versions |
| vulture, interrogate | `[tool.vulture]` / `[tool.interrogate]` in the app's or the root's `pyproject.toml`; a `vulture_whitelist.py` for names only frameworks use |
| deptry | `[tool.deptry]` in the app's `pyproject.toml` |
| import-linter | `[tool.importlinter]` or `.importlinter` in the app |
| semgrep | `.semgrep/` |
| cspell | `cspell.json` (or `.cspell.json`, `cspell.config.*`): add the project's own words here |
| vale | `.vale.ini` |
| betterleaks | `.gitleaks.toml`: allowlist findings you've checked by hand here |
| qlty | `.qlty/qlty.toml` (required: `cq init` writes cq's default) |

The TypeScript tools (eslint, tsc, vitest, knip, npm audit) expect an npm project. A pnpm or Yarn
app is measured, but its vulnerability audit is reported as skipped.

### The gate in detail

| Source | How "new" is decided |
|---|---|
| `qlty check --upstream` (ruff, radarlint, bandit, shellcheck, hadolint, zizmor, osv-scanner) | changed lines only |
| `qlty smells --upstream` | its span covers a changed line |
| aislop, vale, pyscn, fallow, semgrep | run on the branch and the base; the finding's stable key isn't in the base's, in a changed file |
| interrogate (docstrings) | a function, class or module without a docstring that the branch adds or changes |
| betterleaks | the branch's own commits |
| ratchets | suppressions, cognitive complexity, import contracts, deptry, and critical/high vulnerabilities when a lockfile changed: worse than the base |

Each gate run writes `.cq/runs/<time>_gate_<sha>/` (`gate.md`, `gate-findings.jsonl`).

---

## 8. Troubleshooting

| You see | Do this |
|---|---|
| `cq: command not found` | `uv tool install …` ([step 1](#1-set-up-your-machine)). If it's installed, run `uv tool update-shell` and open a new terminal |
| `Permission denied (publickey)` while installing | your ssh key isn't on GitHub, or can't read `rafaeldelrey/cq` |
| `… is not the root of a git repository` | run cq from the repo's root, or pass `--repo <path>` |
| `qlty is not installed` / `qlty X installed, Y pinned` | `cq setup` |
| `Another qlty comes first on your PATH (…)` | remove that qlty, or put `~/.local/bin` before its folder in your PATH |
| `the repo has no .qlty/qlty.toml` | `cq init` |
| `ERROR` next to a tool in the run | read `.cq/runs/<run>/<target>/<tool>.log`: it has the command, its exit code and its output |
| `<tool> is not installed` | `cq doctor` names the install command |
| `not present at this commit` | that app didn't exist at the commit measured (normal in a backfill) |
| a pytest or coverage error | cq runs the app's tests in its own environment (`uv run` in the app's folder): check that `uv run pytest` works there |
| `target names must be unique` | two targets share a name; `repo` is taken by the repo-wide target |
| numbers jumped with no code change | check the report's "Measurement changed" section: a tool version or a config moved |
| the first run is slow | npx, uvx and qlty fetch their tools on first use; later runs are faster |
| `The plugin wasn't installed` during `cq init` | read claude's output above it. Usually ssh access to `rafaeldelrey/cq` is missing; the two `/plugin` lines it prints work from Claude Code too |
| `/code-quality` isn't found in Claude Code | `cq doctor` shows where the plugin is installed; start a new Claude Code session after installing |

---

## 9. Upgrade or uninstall

```bash
cq --version                                                        # what you have
uv tool install --force git+ssh://git@github.com/rafaeldelrey/cq@v0.4.2    # move to the newest tag
uv tool uninstall cq                                                # remove it
```

`uv tool upgrade cq` keeps the tag you installed, so move between versions with
`install --force` at the new tag. A new version can pin newer measuring tools. The first snapshot
after an upgrade then flags the change ("cq: 0.2.0 → 0.3.0"), and [CHANGELOG.md](CHANGELOG.md)
says which numbers can move. To remove a repo's data, delete its `.cq/` folder.

---

## Development

```bash
uv sync
uv run pytest            # coverage floor 85%
uv run ruff check . && uv run ruff format --check . && uv run pyright
uv run cq complexity check cq
```

Changes go through PRs with a Codex review, and CI runs the same checks. Bumping a pinned tool is a
measurement change: say so in the CHANGELOG.

cq was extracted from `rafaeldelrey/angel-monitor`'s `code_quality/` on 2026-10-17
(angel-monitor#391); its history before that date lives there.
