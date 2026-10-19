# The round's review

Two axes, Standards and Spec, in fresh-context subagents launched together. They review the round's commits: `git diff <commit before the round>..HEAD`. In a round with several apps, give each app's part its own Standards reviewer, so no reviewer gets a diff too large to read.

## With Matt Pocock's plugin

When the Skill tool knows `mattpocock-skills:code-review`, invoke it with the parent of the round's first commit as the fixed point. Its sources:
- **Standards:** the repo's code standards and review rules from step 1, and the out-of-bounds paths;
- **Spec:** the decision from step 4: the config policy and the scope.

## Without it

Run Claude Code's built-in `/code-review` on the round's diff. It looks for correctness bugs and cleanups, but has no spec axis, so also launch one `general-purpose` subagent as the **Spec** reviewer, with this brief:

> Review this round of a code-quality loop against its decision (<the decision text>). Report:
> (a) changes outside the scope, or inside an out-of-bounds path;
> (b) config changes that hide real findings, not findings that are wrong for this repo;
> (c) behaviour changes in what were meant to be refactors;
> (d) changed production lines without tests.
> Quote the decision line for each. Under 300 words.

The report says which review ran.

## For every reviewer

- **Read the repo's rules** where step 1 found them. Stay read-only, and run nothing that changes the machine or the user's config.
- **Look hardest at:**
  - a refactor's behaviour;
  - output that must stay comparable, such as finding keys and metrics;
  - tests that only check a mock was called;
  - any tool total that rose since the last round. The round's own new code brought those findings in.
