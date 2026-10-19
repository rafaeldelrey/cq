---
name: improve-code-quality
description: Improve a repo's code quality in measured rounds, ending at a ready PR and a report.
disable-model-invocation: true
---

# /improve-code-quality

An unattended loop of **rounds** on the repo you are in: measure with cq, fix, commit, review, fix, check, until the stop rule the user picks. The work lands the way the user chooses, at best as a PR that is **ready** ([references/follow-up.md](references/follow-up.md) defines it). Landing it is always the user's call: the loop never merges.

The words the loop runs on:
- **Round:** one pass through steps 5–10, ending in its commits and its own measurement. Round 1 starts from step 4's run; each later round starts from the run that ended the round before.
- **Total:** the sum of every tool's own count, taken from the snapshot (step 5), never from finding rows.
- **Plateau:** two rounds in a row that each move the total by under 10%, or by fewer than 5.
- **Out of bounds:** the paths the loop never changes (step 1).
- **Config ledger:** every config change the loop makes, with what it hides and why that is wrong for this repo.

## 1. The repo's rules

Read `AGENTS.md` (or `CLAUDE.md`) and the rule files it links. A repo that keeps them local (gitignored) has them only in the main checkout, so read them from there.

- **With a `## Shipping` section:** its checks, quality gate, version and changelog rule, PR title and body format and review rules are the loop's.
- **Without one:** the defaults.
  - The checks are the ones the repo already has, found in its config and lockfiles: ruff, pyright and pytest for Python; eslint, tsc and vitest for TypeScript.
  - The PR body is plain, and there is no version bump.

**Out of bounds:** every area whose route needs evidence before a PR, such as agent prompts and tools, a guard, a danger path or a model. Write the bounds so a reviewer can check them:
- whole folders (`app/agents/`);
- the single files every agent runs through;
- files picked out by a marker matched on its definition (`^\s*@tool`), not on a mention.

Text a model or an API reads, such as tool docstrings or `Field(description=…)`, is behaviour there.

Done when you can name the checks (gate included), the version rule (or "none"), and every out-of-bounds path and marker.

## 2. The worktree

Make a branch and a worktree from `origin/main` beside the repo (`../<repo>.worktrees/quality-loop-<date>`). Copy in from the main checkout any untracked files the checks need (env files). Everything from here on, setup included, happens in the worktree.

Done when `git -C <worktree> status` is clean on the new branch.

## 3. cq is ready, on the right targets

In the worktree, run `cq --version`, `cq doctor` and `cq run --tool size --no-history`, and read the targets it measured.

- **cq isn't ready:** `cq` or a required tool is missing, or the repo isn't set up (no cq settings and no `.qlty/qlty.toml`). Invoke `/cq:setup-cq`, which asks before installing anything.
- **The targets aren't the repo's apps:** for example, one root target where the repo ships several apps. Write `[[tool.cq.targets]]` (or `cq.toml`) from the repo's own records (an older config, CI scripts), and show the user before using them.
- **A target's `image`** must name what is deployed today. Check it against the app's build or deploy script: a registry move leaves an old tag behind.
- **Install each app's dependencies** with its own package manager (`npm ci`, `pnpm`, `yarn`, `uv sync`). A fresh worktree has none.
- **Commit what this step wrote** as **round 0**.

Note the `cq --version`, and measure every round with that same cq.

Done when every app the repo ships is a target, the size run succeeds, and the worktree is clean.

## 4. The questions

Run `cq run` in the worktree. This run is the baseline and round 1's measurement. Ask the questions in [references/questions.md](references/questions.md) with AskUserQuestion, filled in with this run's numbers.

Record the answers as a decision:
- **On the repo's tracking issue, when there is one:** the one the user named, or an open issue about code quality (`gh issue list --search "code quality"`). Ask which one if several fit.
- **Otherwise,** in `decisions.md` in this run's folder (`.cq/runs/<run>/`).

The PR body repeats the decision.

Done when the stop rule, the config policy, the landing and the scope are each answered and recorded.

## 5. Pick the round's findings

Start from the latest complete run: step 4's in round 1, and the one step 10 took after that. Pick the findings to work on with `jq` on `.cq/runs/<run>/findings.jsonl`.

**A run is complete** when every tool the baseline measured has a measurement in it. `cq run` exits 0 even when a collector fails, and records the failure in `snapshot.json`'s `errors` (or as a `skipped` with its reason). A tool missing from a run is a blind spot, never a fall to zero. Run it again once; if the tool still fails, stop and ask the user. Never compare, stop on or report a total from an incomplete run.

**Totals come from the snapshot.** cq lists only each tool's worst items, with a cap that varies by tool. Once those are fixed the next ones fill the list, so a row count can stay flat while the real number falls. Take each tool's count from `snapshot.json` (`docstrings.missing`, `smells.smells`, `aislop.findings`, `radarlint.findings`, …), and use rows only for tools that list everything. Low-coverage files are capped too: count the files under 50% from the app's own coverage report.

Done when the run is complete and you have the list of findings this round will work on.

## 6. Reduce, and commit

Work through the findings, most severe first.

- **A real finding** is fixed in code, with tests for every changed production line.
- **A false positive** (wrong for this repo, such as a project word flagged as a typo, or a dependency that runs as a command) goes into the repo's own tool config, if the policy allows it.
  - First read the config file's own rules. A file can forbid an entry: a spell-check list that never takes names leaves a name in code to be fixed in code.
  - Add the entry in the file's own layout, with its reason where the format allows.
  - Record it in the config ledger.
- **Out-of-bounds and out-of-scope findings** are left alone and listed.

[references/multi-app.md](references/multi-app.md) covers three special cases:
- a repo with several apps (one fix agent each, in parallel);
- image vulnerabilities;
- a repo whose own code produces findings or metrics.

Commit the round: one commit per app, each message naming the round and what it changed.

Done when every finding of the round is fixed, in the ledger, or listed with its reason, and the round is committed.

## 7. Review

Run the review in [references/review.md](references/review.md) on this round's commits.

Done when both axes have reported.

## 8. Fix the review

Every review finding gets an outcome: **fixed**, or **declined** with its reason. A declined hard violation of a documented standard is a stop: ask the user. Commit the fixes per app, as the round's review-fix commits.

Done when every finding has its outcome, written down for the PR body, and the fixes are committed.

## 9. Check

Run every check from step 1: each app's, the repo's quality gate, and the tests of any shared config the round changed. Fix what fails and commit the fix.

**A gate that judges the files a branch touches** charges each touched file's old findings to the branch, even when the only edit was a docstring. Fix them in this round.

Done when every check passes and the worktree is clean.

## 10. Measure, then stop or go on

Run `cq run` on the round's last commit, and check that it is complete (step 5). This run is the round's result: compare its total with the run the round started from. It is also where the next round starts, and the last one is the report's "after".

- **Stop** when the user's stop rule is met, after a round that removed nothing, or after round 10.
- **At a plateau,** look at what is left. One more round is allowed, once, if a warning or error is still fixable in code within scope. Say so. That round counts toward the 10. Info findings, such as a low-coverage file or a missing docstring, go in the report and never extend the loop.
- **Otherwise** go on. With a PR per round, hand this round over first (step 12). Once the user has landed it, start the next round on a fresh branch from the new `origin/main`. Then go to step 5.

Done when the round's run is complete and compared, and the loop has stopped or the next round has started.

## 11. The report

Write it from [references/report.md](references/report.md). Publish it as a page when the session can; otherwise write `report.md` in the last run folder.

Done when the report has every section in report.md, with numbers from the runs.

## 12. Hand it over

- **The branch only:** give the user the branch, the worktree and the report.
- **A PR,** at the end or for this round: follow [references/follow-up.md](references/follow-up.md) until the PR is ready. Then give the user the PR link and the report, and ask whether to land it.

Done when the user has the link (or the branch) and the report, and has been asked.
