# The report

The PR body and the page both carry it. Every number comes from a run folder: name each run by its id.

## 1. The rounds

A table with one row per round, plus the starting snapshot:
- findings, and warnings;
- coverage, docstrings, near-duplicate code, the worst complexity, aislop's score;
- what the round did, in one line.

Then the stop: which rule ended the loop, and the totals of the last two rounds.

## 2. What code fixed, and what config hid

Revert the loop's config changes in a scratch copy of the final branch (every entry of the config ledger), run the same cq on it, and give three totals:

| | Findings | Warnings |
|---|--:|--:|
| Before the loop | | |
| After, code changes only (config reverted) | | |
| After, as the branch is | | |

Name the tools behind each difference. Lead the summary with this split, not with the combined drop alone.

## 3. The tool held fixed (when the repo is cq)

When the repo is cq itself, the loop changed the measuring tool. Install the old and new cq into throwaway tool folders (`UV_TOOL_DIR`, `UV_TOOL_BIN_DIR`). Run both on a clone of the code from before the loop and on a clone from after. List every finding key one version reports and the other doesn't. Expected: none. Explain any metric that differs.

## 4. The config ledger

Each entry gives:
- the file and the setting;
- the findings it hides, with their count;
- why they are wrong for this repo.

Mark the arguable ones as arguable.

## 5. The reviews

For each round, every finding and its outcome: fixed, or declined with its reason. Say which review ran each time.

## 6. What remains

Every finding left, each with its reason:
- out of scope;
- out of bounds (and which route);
- a measurement change that needs its own evidence;
- a trade-off where a fix would only move the number.

Then the issues to propose (proposed, not created).
