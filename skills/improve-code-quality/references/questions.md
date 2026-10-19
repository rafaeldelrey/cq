# The four questions

Ask all four in one AskUserQuestion call. The recommended option goes first, marked "(Recommended)".

**Explain every reference.** An option that names an issue, an issue section, a tool or a rule says what it is in a few words, in the option itself, so the user can answer without looking anything up. "#5 §4" alone is unanswerable. "#5 §4: real tests for the TypeScript collectors" is answerable.

Fill each question with this repo's numbers from the first `cq run`.

## 1. When should the loop stop?

Say in the question that it always stops after a round that removes nothing, or after round 10, and reports where it got to.

- **Until it plateaus:** two rounds in a row that each move the findings by under 10%, or by fewer than 5.
- **Concrete targets:**
  - no errors;
  - every warning fixed or recorded as a reasoned false positive;
  - coverage ≥ 90%;
  - no function over cyclomatic or cognitive complexity 15;
  - every file's maintainability index ≥ 50;
  - aislop ≥ 95;
  - docstrings 100%;
  - a review round with no hard findings.
- **Stricter:** the same, plus complexity ≤ 10, coverage ≥ 95% and duplication under 2%.

## 2. May the loop change tool config to silence false positives?

Name this repo's likely noise with its counts, for example "62 print findings in the CLI modules, 150 cspell hits on tool names".

- **Yes, with reasons:** each change is recorded with what it hides, and real findings are always fixed in code.
- **Code only:** config is never touched, and the noise stays in the numbers.

## 3. How should the work land?

- **One PR at the end:** followed until CI and Codex have cleared it, then handed over.
- **A PR per round:** each one cleared and landed before the next round starts.
- **The branch only:** the user reviews it.

## 4. Which bigger open items belong in the loop?

Use `multiSelect: true`. List up to three of the repo's open issues or issue sections that the findings touch, each with its gloss, plus **"None of these"**. Leave this question out when there are none.
