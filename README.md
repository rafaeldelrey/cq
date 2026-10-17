# cq

Code-quality snapshots of a repository, stored as history and reported as trends, plus a PR gate
that judges only what a branch adds. About thirty collectors:

- **Correctness and tests:** coverage, pytest results, pyright, ruff.
- **Complexity:** cyclomatic complexity, cognitive complexity, maintainability.
- **Code health:** dead code, duplication, docstrings, house rules (semgrep), import contracts.
- **Dependencies:** deptry/knip hygiene, freshness, vulnerabilities.
- **Security:** secrets in the tree and the history, plus which keys are still live.
- **Test strength:** mutation testing, mock-only tests.
- **Repo hygiene:** churn × complexity hotspots, suppressions, Dockerfile and image scans, shell
  and workflow linting, doc links.
- **AI slop:** radarlint, smells, aislop, pyscn, fallow, vale, cspell.

Extracted from `rafaeldelrey/angel-monitor`'s `code_quality/` on 2026-10-17 (angel-monitor#391);
its history before that date lives there.

## Install

```bash
uv tool install git+ssh://git@github.com/rafaeldelrey/cq    # add @v0.1.0 to pin
cq doctor        # which measuring tools are installed, and the command for each missing one
cq setup         # installs them: Homebrew, and qlty from its release (checksum + attestation)
```

The repo is private, so the install needs GitHub ssh access. The Python tools come with cq. The
npx and uvx tools are fetched on first use. Homebrew installs the rest.

## Use

```bash
cd my-repo
cq init                          # optional: starter settings and .qlty/qlty.toml, asking first
cq run                           # snapshot HEAD → .cq/history/ and .cq/runs/<id>/
cq gate --base origin/main       # what this branch adds over its base
cq report                        # re-render the latest run's report.md and dashboard.html
cq backfill --since 2026-06-01   # past commits into the trend, one every 14 days
cq complexity check src          # the cyclomatic-complexity ratchet (check | report | baseline)
```

`cq run --target <t> --tool <tool>` runs a subset; a partial run stays out of history.
`cq run --rev <sha>` measures a past commit in a temporary worktree outside the repo. `--repo`
measures another folder, and `--config` (or `CQ_CONFIG`) reads a settings file other than the
defaults, relative to the repo.

`.cq/` ignores itself, so nothing cq writes is ever committed.

## Settings

A repo with a `pyproject.toml` at its root needs no settings. cq measures it as one Python app,
whose production code is `src/`, else its package, else its top-level packages, with `tests/`
when present, plus the `repo` target (secrets, scripts, links, prose).

Anything else goes in `cq.toml` at the root, or in `[tool.cq]` in `pyproject.toml`. The keys are
the same in both; in `cq.toml` they sit at the top level.

```toml
[tool.cq]
data_dir = ".cq"                 # history/ and runs/
complexity_limit = 15
cognitive_limit = 15
shell_roots = ["."]              # where shellcheck looks for tracked *.sh
link_paths = ["README.md", "docs"]
link_exclude = []
complexity_baseline = ".complexity-baseline.json"
# The repo's own config for a tool, when it isn't where cq looks by default:
semgrep_rules = ""               # a folder: *.yml production rules, tests/*.yml test rules
contracts_dir = ""               # import-linter contracts as <target>.ini
vulture_config = ""              # a toml with [tool.vulture], for every Python app
interrogate_config = ""          # a toml with [tool.interrogate], for every Python app
cspell_config = ""
vale_config = ""
gitleaks_config = ""

[[tool.cq.targets]]
name = "api"
kind = "python"                  # python | typescript | repo
root = "apps/api"
source = ["app"]
tests = true
deptry_ignore = ["DEP002=uvicorn"]
mutation_paths = ["app/core/rules.py"]
image = "me/api:latest"          # Trivy scans it

[tool.cq.gate]
mode = "warn"                    # block: exit 1 on a blocking finding or a tool that didn't run
block = ["secrets:*", "semgrep:cq-*", "smells:*", "ratchet:*"]
```

### Which config each tool reads

Each tool reads, in order:

1. a file the settings name;
2. the repo's own config for that tool;
3. cq's default (in `cq/defaults/`).

| Tool | The repo's own config |
|---|---|
| ruff, pyright, pytest, coverage | the app's `pyproject.toml`, always (they run in the app's own environment) |
| vulture, interrogate | `[tool.vulture]`, `[tool.interrogate]` in the app's or the root's `pyproject.toml`; a `vulture_whitelist.py` |
| deptry | `[tool.deptry]` in the app's `pyproject.toml` |
| import-linter | `[tool.importlinter]` or `.importlinter` in the app |
| semgrep | `.semgrep/` |
| cspell | `cspell.json` (or `.cspell.json`, `cspell.config.*`) |
| vale | `.vale.ini` |
| betterleaks | `.gitleaks.toml` |
| qlty | `.qlty/qlty.toml` (required: `cq init` writes cq's default) |

Every snapshot records which file each tool read and a hash of it. When a tool version or a config
changed since the previous snapshot, the report and the dashboard say so above the numbers, so a
step that comes from the measurement isn't read as a change in the code.

### Tool versions

cq pins its measuring tools per release:
- the Python tools in its `pyproject.toml`;
- the npx/uvx tools and qlty in `cq/config.py` (`PINS`).

Without the pins, two snapshots aren't comparable. A snapshot records every version in
`tool_versions`, so a cq upgrade that moves a number can be told apart from a change in the code.

## The PR gate

`cq gate` judges only what the branch adds:

| Source | How "new" is decided |
|---|---|
| `qlty check --upstream` (ruff, radarlint, bandit, shellcheck, hadolint, zizmor, osv-scanner) | changed lines only |
| `qlty smells --upstream` | its span covers a changed line |
| aislop, vale, pyscn, fallow, semgrep | run on branch and base; the finding's stable key is not in the base's, in a changed file |
| interrogate (docstrings) | a function, class or module without a docstring that the branch adds or changes |
| betterleaks | the branch's commits |
| ratchets | suppressions, cognitive complexity, import contracts, deptry, critical/high vulnerabilities when a lockfile changed: worse than the base |

It writes `runs/<timestamp>_gate_<sha>/` (`gate-findings.jsonl`, `gate.md`) and appends `gate.md`
to `$GITHUB_STEP_SUMMARY` on GitHub Actions.

## Claude Code plugin

The repo is also a Claude Code plugin marketplace, laid out like `mattpocock-skills`:

```
/plugin marketplace add rafaeldelrey/cq
/plugin install cq@cq
```

- `/code-quality`: run, report, backfill and gate, plus the written analysis on the dashboard.
- `/setup-cq`: walks a repo through `cq doctor`, `cq setup`, `cq init` and the first run.

## Development

```bash
uv sync
uv run pytest            # coverage floor 85%
uv run ruff check . && uv run ruff format --check . && uv run pyright
```

Changes go through PRs with a Codex review. Bumping a pinned tool is a measurement change: say so
in the CHANGELOG.
