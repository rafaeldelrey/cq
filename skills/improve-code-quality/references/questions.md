# The questions

Ask them in one AskUserQuestion call, with the recommended option first, marked "(Recommended)". Fill each in with this repo's numbers from step 4's run.

**Explain every reference in the option itself.** An option that names an issue, an issue section, a tool or a metric says what it is in a few words, so the user can answer without looking anything up. "#5 §4" alone can't be answered; "#5 §4: real tests for the TypeScript collectors" can.

## 1. When should the loop stop?

The question also says that the loop always stops after a round that removes nothing, or after round 10, and reports where it got to.

- **Until it plateaus:** two rounds in a row that each move the total by under 10%, or by fewer than 5.
- **Concrete targets:**
  - no errors;
  - every warning fixed or recorded as a reasoned false positive;
  - coverage ≥ 90%;
  - no function over cyclomatic or cognitive complexity 15;
  - every file's maintainability index ≥ 50;
  - aislop's score (its 0–100 rating for AI-agent leftovers) ≥ 95;
  - docstrings 100%;
  - a review round with no hard findings.
- **Stricter:** the same, plus complexity ≤ 10, coverage ≥ 95% and duplication under 2%.

## 2. May the loop change tool config to silence false positives?

Name this repo's likely noise with its counts and a gloss, for example: "150 cspell (spell-checker) hits on tool names; 62 semgrep print findings in the CLI's own modules".

- **Yes, with reasons:** each change goes in the config ledger with what it hides. Real findings are always fixed in code.
- **Code only:** config is never touched, and the noise stays in the numbers.

## 3. How should the work land?

- **One PR at the end:** followed until it is ready, then handed to the user to land.
- **A PR per round:** each followed until it is ready and handed over; the next round starts after the user lands it.
- **The branch only:** the user reviews it.

## 4. Which bigger open items belong in the loop?

Use `multiSelect: true`. List up to three of the repo's open issues or issue sections that the findings touch, each with its gloss, plus **"None of these"**. Leave the question out when there are none.
