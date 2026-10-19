# The round's review

Two axes, in two fresh-context subagents launched together. The diff is the round's commits: `git diff <commit before the round>..HEAD`.

## With Matt Pocock's plugin

When the Skill tool knows `mattpocock-skills:code-review`, invoke it with the round's first commit's parent as the fixed point. Give it these sources:
- **Standards:** the repo's code standards and review rules from step 1, and the out-of-bounds paths.
- **Spec:** the decision from step 4: the config policy and the scope.

## Without it

Run Claude Code's built-in `/code-review` on the round's diff. It looks for correctness bugs and cleanups, and it has no spec axis. So also launch one `general-purpose` subagent as the **Spec** reviewer, with this brief:

> Review this round of a code-quality loop against its decision (<the decision text>). Report:
> (a) changes outside the scope or inside an out-of-bounds path;
> (b) config changes that hide real findings rather than findings that are wrong for this repo;
> (c) behaviour changes in what were meant to be refactors;
> (d) changed production lines without tests.
> Quote the decision line for each. Under 300 words.

The report says which review ran.

## Every review, both ways

- **Tell each reviewer:** read the rules from the main checkout, run nothing that changes the machine or the user's config, and stay read-only.
- **What deserves the hardest look:** a refactor's behaviour, output that must stay comparable (finding keys, metrics), and tests that only check that a mock was called.
