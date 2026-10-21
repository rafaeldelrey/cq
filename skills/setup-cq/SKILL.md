---
name: setup-cq
description: Set a repository up for cq, the code-quality CLI. Installs cq if it is missing, checks and installs the measuring tools (cq doctor, cq setup), adds the repo's starter settings and qlty config (cq init), and takes the first snapshot. Use when the user types /setup-cq, asks to set up cq or code-quality measurement in a repo, or when /code-quality finds cq missing.
---

# /setup-cq: get a repo ready for cq

Work from the repo's root. Ask before every step that installs something or changes a file, and
show the exact command first. Nothing here is committed for the user: say which files changed and
let them commit.

## 1. The cq command

Check with `cq --version` (or `command -v cq`). If it is missing:

```bash
uv tool install git+ssh://git@github.com/rafaeldelrey/cq@v0.6.0
```

Use the newest release tag (`git ls-remote --tags git@github.com:rafaeldelrey/cq.git`): a tag pins
the measuring tools, so snapshots stay comparable. The repo is private, so this needs the user's
GitHub ssh access. If `uv` is missing, the user installs it first (`brew install uv`).

## 2. The measuring tools

Run `cq doctor`. It lists every system tool cq runs, the version found, and the command that
installs each missing one; qlty must be at the version this cq pins.

If anything required is missing, show the user doctor's list and ask before running `cq setup`.
It shows its plan, then runs `brew install` for the missing tools and installs qlty from its GitHub
release after checking the checksum and the build attestation. Run it as `cq setup --yes` only
after the user said yes to that plan. Optional tools (hadolint, trivy) can stay missing: their
collectors skip when a repo has no Dockerfile or image.

## 3. The repo

Run `cq init` without `--yes` first if the user wants to answer each question in the terminal;
otherwise describe what it will do and run it with the answers they chose:

- **Settings:** a starter `[tool.cq]` in `pyproject.toml` (or `cq.toml` when there is none) with
  the PR gate in `warn` mode. Targets stay detected: a `pyproject.toml` at the root is one Python
  app (`src/` or its package, `tests/` if present). A monorepo needs `[[tool.cq.targets]]`
  entries: ask the user for each app's root, kind and production folders.
- **qlty's config:** `.qlty/qlty.toml`, cq's default, when the repo has none. Without it the
  qlty-based tools (radarlint, smells, workflow security, the gate's line checks) report an error.
- **This plugin:** it is already installed (this skill is running), for the user or for the repo.
  `cq init` says which. If it is installed only for the user, ask whether the team should get it
  too. On a yes, pass `--skills project`: that writes the repo's `.claude/settings.json`, which the
  user commits. Otherwise pass `--skills none`.

So the usual run is `cq init --yes --skills none` (or `--skills project`).

Then point out the repo's own configs cq will read if they exist, and offer to add the ones that
matter for this repo: a `cspell.json` with its technical terms, a `.gitleaks.toml` allowlist for
findings checked by hand, a `.semgrep/` folder of house rules, import-linter contracts.

## 4. The first snapshot

Run `cq run`. Report how many tools measured and which ones errored, with the first line of each
error from `runs/<run>/<target>/<tool>.log`. Then offer `/code-quality report` for the analysis, and
`cq backfill --since <date> --dry-run` to give the trend a history.

## 5. For a team

- In CI, the gate is `cq gate --base origin/main` after installing cq and the tools. Its summary
  goes to the job summary on GitHub Actions. It stays in `warn` mode until the team agrees to
  `block`.
- Colleagues get these skills from the repo's `.claude/settings.json` once the plugin is installed
  for the project (step 3). They need read access to `rafaeldelrey/cq`.
