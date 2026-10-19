---
name: improve-code-quality
description: Improve a repo's code quality in rounds (measure with cq, fix, review, fix, test, commit) until it plateaus or meets set targets, then hand over a PR that CI and Codex have already cleared, with a report of what code fixed and what config hid.
disable-model-invocation: true
argument-hint: "[resume]"
---

# /improve-code-quality

An unattended loop of **rounds** on the repo you are in. Each round measures with cq, fixes what it reports, reviews the fixes, fixes what the review finds, runs the tests and commits. The loop stops by the rule the user picks. If the answer was a PR, it ends with a PR that is **ready**: CI is green on the head, Codex has reviewed the head, and no thread is open. Then it asks the user. It never merges.

Two words carry the loop:
- **Round:** one pass through steps 4–9, ending in its commits.
- **Plateau:** two rounds in a row that each move the findings total by less than 10% of the round before, or by fewer than 5 findings.

## 1. cq is ready

Run `cq --version` and `cq doctor` from the repo's root. If `cq` is missing, `doctor` reports a required tool missing, or the repo has no cq settings and no `.qlty/qlty.toml`, invoke `/cq:setup-cq` (it asks before installing anything), then continue.

Done when `cq doctor` shows every required tool present and `cq run --tool size` succeeds.

## 2. The repo's rules

Read `AGENTS.md` (or `CLAUDE.md`) and any rule files it links.

- **With a `## Shipping` section:** its checks, version and changelog rule, PR body format, review rules and merge gate are the loop's.
  - Every area whose route needs evidence before a PR (agent prompts, a danger path, a model) is **out of bounds**. Make no change there, and list it for the report.
- **Without one:** the defaults.
  - The checks are the ones the repo already has: ruff, pyright and pytest for Python; eslint, tsc and vitest for TypeScript. Find them in its config and lockfiles.
  - The PR body is plain, and there is no version bump.

Done when you can name the checks, the version rule (or "none"), and every out-of-bounds path.

## 3. The four questions

Ask them with AskUserQuestion, from [references/questions.md](references/questions.md), filled in with this repo's numbers from a first `cq run`. Then record the answers as a decision: a comment on the tracking issue if the user names one, or otherwise `decisions.md` in the first run folder. The PR body repeats them.

Done when the stop rule, the config policy, the landing and the scope are each answered and recorded.

## 4. The worktree

Make a branch and a worktree from `origin/main`, beside the repo (`../<repo>.worktrees/quality-loop-<date>`). Copy in any untracked files the checks need (env files). All work happens there, never in the main checkout. Note the `cq --version` you measure with, and use that same cq in every round, so the rounds compare.

Done when `git -C <worktree> status` is clean on the new branch.

## 5. Measure

Run `cq run` in the worktree. Read the findings with `jq` on `runs/<run>/findings.jsonl`, and compare with the previous round's run.

Done when you have the totals by tool and severity, and the list of findings this round will work on.

## 6. Reduce

Work through the findings, most severe first.

- **A real finding** is fixed in code, with tests for every changed production line.
- **A false positive** (wrong for this repo: a tool's own name flagged as a typo, a dependency that runs as a command) goes into the repo's own tool config, if the policy allows it. Add a reason in the config itself, and an entry in the round's **config ledger**: what it hides and why that is wrong for this repo.
- **Out-of-bounds and out-of-scope findings** are left alone and listed.

**When the repo's own code produces findings or metrics** (the repo is cq, or another analysis tool), a refactor of that code must keep its output identical. Record the output on stored snapshots before the change and compare after; a committed golden fixture is better.

Done when every finding of the round is fixed, put in the ledger, or listed with its reason.

## 7. Review

Run the review in [references/review.md](references/review.md) on this round's commits, in fresh-context subagents.

Done when both axes have reported.

## 8. Fix the review

Every review finding gets an outcome: **fixed**, or **declined** with its reason. A declined hard violation of a documented standard is a stop: ask the user.

Done when every finding has its outcome written down for the PR body.

## 9. Test and commit

Run every check from step 2 and fix what fails. Commit the round, and its review fixes as a second commit. Each message names the round and what it changed.

Done when the checks pass and the worktree is clean.

## 10. Stop or go on

Compare this round's totals with the last round's.

- **Stop** when the user's rule is met, at a plateau (when that was the rule), after a round that removed nothing, or after round 10.
- **Otherwise** go back to step 5.

At a plateau, look at every finding left. If one is still fixable in code within scope, do one more round for it, and say so.

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
