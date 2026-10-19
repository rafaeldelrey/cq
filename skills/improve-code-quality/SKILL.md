---
name: improve-code-quality
description: Improve a repo's code quality in rounds (measure with cq, fix, review, fix, test, commit) until it plateaus or meets set targets, then hand over a PR that CI and Codex have already cleared, with a report of what code fixed and what config hid.
disable-model-invocation: true
argument-hint: "[resume]"
---

# /improve-code-quality

An unattended loop of **rounds** on the repo you are in. Each round measures with cq, fixes what it reports, reviews the fixes, fixes what the review finds, runs the tests and commits. The loop stops by the rule the user picks. If the answer was a PR, it ends with a PR that is **ready**: CI is green on the head, Codex has reviewed the head, and no thread is open. Then it asks the user. It never merges.

Two words carry the loop:
- **Round:** one pass through steps 5–9, ending in its commits.
- **Plateau:** two rounds in a row that each move the findings total by less than 10% of the round before, or by fewer than 5 findings.

## 1. The repo's rules

Read `AGENTS.md` (or `CLAUDE.md`) and any rule files it links.

- **With a `## Shipping` section:** its checks, version and changelog rule, PR title and body format, review rules and merge gate are the loop's.
- **Without one:** the defaults.
  - The checks are the ones the repo already has: ruff, pyright and pytest for Python; eslint, tsc and vitest for TypeScript. Find them in its config and lockfiles.
  - The PR body is plain, and there is no version bump.

**Out of bounds:** every area whose route needs evidence before a PR, such as agent prompts and tools, a danger path or a model. Write the bounds as paths a reviewer can check:
- whole folders (`app/agents/`);
- plus files picked out by a marker (every file defining an `@tool`, or a Pydantic `Field(description=…)` a model reads).

Match the marker on its definition, not on a mention: `^\s*@tool`, not `@tool` anywhere. In angel-monitor, a loose search listed 11 files that only mentioned `@tool` in comments, and none that defined one.

Docstrings and descriptions that a model or an API reads count as behaviour there, so a docstring added in those places alters an agent. The loop makes no change inside the bounds, and lists them for the report.

Done when you can name the checks, the version rule (or "none"), and the out-of-bounds paths and markers.

## 2. The worktree

Make a branch and a worktree from `origin/main`, beside the repo (`../<repo>.worktrees/quality-loop-<date>`). Copy in any untracked files the checks need (env files) from the main checkout. All work happens in the worktree from here on, setup included: settings written in the main checkout never reach it.

Done when `git -C <worktree> status` is clean on the new branch.

## 3. cq is ready, on the right targets

In the worktree, run `cq --version` and `cq doctor`, then `cq run --tool size --no-history`, and read the targets it measured.
- If `cq` is missing or a required tool is missing, invoke `/cq:setup-cq`. It asks before installing anything.
- **If the targets aren't the repo's apps,** settings are needed. That happens when cq found one root target in a monorepo of `apps/*`, or the repo already lists its apps elsewhere (an older config, CI scripts). Write them as `[[tool.cq.targets]]` (or `cq.toml`), from the repo's own records, and show the user before using them.
- **A target's `image`** must name what is deployed today. Check it against the app's build or deploy script. A registry move leaves the old tag behind, and Trivy then judges a stale build.
- **Install each app's dependencies in the worktree** with its own package manager: `npm ci` in a TypeScript app (`pnpm`/`yarn` per its lockfile), `uv sync` in a Python app. A fresh worktree has none, and cq reports the npm tools as "node_modules missing".
- Commit any settings this step wrote as **round 0**, so every round measures the same way.

Note the `cq --version` you measure with, and use that same cq in every round, so the rounds compare.

Done when every app the repo ships is a target, the size run succeeds, and the worktree is clean.

## 4. The four questions

Run `cq run` once in the worktree for this repo's numbers. Then ask the questions from [references/questions.md](references/questions.md) with AskUserQuestion. Record the answers as a decision: a comment on the tracking issue if the user names one, or otherwise `decisions.md` in that first run folder. The PR body repeats them.

Done when the stop rule, the config policy, the landing and the scope are each answered and recorded.

## 5. Measure

Run `cq run` in the worktree. Read the findings to work on with `jq` on `runs/<run>/findings.jsonl`.

**Count from the snapshot, not from `findings.jsonl`.** cq lists only each tool's worst items in a snapshot (200 docstrings, 120 for several tools). Once those are fixed, the next ones fill the list, so a finding count can stay flat while the real number falls. Take each tool's total from its own count in `snapshot.json` (`docstrings.missing`, `smells.smells`, `aislop.findings`, `radarlint.findings`, …). Use the finding rows for tools that list everything. The rounds, the plateau and the report use these totals.
- **Low-coverage files** are the worst 15 only. Covering them brings the next 15 up, so count the files under 50% from the app's own coverage report (coverage.py's JSON, or vitest's `coverage-summary.json`).

Done when you have each tool's total from the snapshot, compared with the previous round's, and the list of findings this round will work on.

## 6. Reduce

Work through the findings, most severe first.

- **A real finding** is fixed in code, with tests for every changed production line.
- **A false positive** (wrong for this repo: a tool's own name flagged as a typo, a dependency that runs as a command) goes into the repo's own tool config, if the policy allows it. Add a reason in the config itself, and an entry in the round's **config ledger**: what it hides and why that is wrong for this repo.
  - **Read the config file's own rules first.** angel-monitor's `cspell.json` says personal names are never added: "a name in production code stays a finding". Under such a rule, the finding is fixed in code (a neutral example name), not allowed in config.
  - **Add to a config file in its own layout.** Rewriting the whole file to add one entry makes a diff nobody can review.
- **Out-of-bounds and out-of-scope findings** are left alone and listed.
- **Image vulnerabilities** describe the published image, so cq's numbers move only after a deploy. A Dockerfile fix is checked on a local build that is never pushed, with `trivy image` before and after.

**In a repo with several apps,** give each app its own fix agent, in parallel, in the same worktree. Each agent edits only its app's folder, proposes config instead of editing shared config, and runs that app's checks. It doesn't commit; you do, once all have reported.
- **Each agent re-runs the relevant tools on its own folder before reporting,** so it catches the findings its new code adds. In angel-monitor's round 2, extracted modules brought in 6 cspell, 8 knip and 13 radarlint findings that the agent never saw.
- **Every tool total that rises from one round to the next is a review finding** for that round.
- **Commit each app's part of a round as its own commit,** and keep the evidence for "behaviour unchanged" (rendered-HTML diffs, golden outputs) for the PR body. One 13,000-line commit can't be reviewed.
- **An agent that starts helpers reports only after every helper has finished.** In angel-monitor's round 2, a fix agent handed back while five helpers were still editing.
- **Before you commit or measure,** check that no agent is still running, and that `git diff --stat` stays inside the agents' folders.

**When the repo's own code produces findings or metrics** (the repo is cq, or another analysis tool), a refactor of that code must keep its output identical. Record the output on stored snapshots before the change and compare after; a committed golden fixture is better.

Done when every finding of the round is fixed, put in the ledger, or listed with its reason.

## 7. Review

Run the review in [references/review.md](references/review.md) on this round's commits, in fresh-context subagents.

Done when both axes have reported.

## 8. Fix the review

Every review finding gets an outcome: **fixed**, or **declined** with its reason. A declined hard violation of a documented standard is a stop: ask the user.

Done when every finding has its outcome written down for the PR body.

## 9. Test and commit

Run every check from step 1 and fix what fails. Commit the round, and its review fixes as a second commit. Each message names the round and what it changed.

Done when the checks pass and the worktree is clean.

## 10. Stop or go on

Compare this round's totals with the last round's.

- **Stop** when the user's rule is met, at a plateau (when that was the rule), after a round that removed nothing, or after round 10.
- **Otherwise** go back to step 5.

At a plateau, look at every finding left. If a **warning or error** is still fixable in code within scope, do one more round for it, and say so. Info findings (a low-coverage file, a missing docstring) are listed in the report. Covering one more file can always bring up the next, so they never extend the loop.

Done when the rule says stop, or the next round has started.

## 11. The report

Write it with [references/report.md](references/report.md): the rounds, what code fixed against what config hid, the config ledger, every review outcome, and every finding left with its reason. Publish it as a page when this session can; otherwise write `report.md` in the last run folder.

Done when the report holds every section in report.md, with numbers from the runs.

## 12. Land it

Follow the landing the user chose:
- **The branch only:** stop, and give the branch, the worktree and the report.
- **A PR, once or per round:** follow [references/follow-up.md](references/follow-up.md) until the PR is ready.

Then call the user with the PR link and the report, and ask whether to land it.

Done when the user has the PR link, the report and that question, or the branch and the report.
