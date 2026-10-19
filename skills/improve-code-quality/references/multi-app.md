# Special cases in step 6

## A repo with several apps

Give each app its own fix agent, in parallel, in the same worktree:
- **Each agent edits only its app's folder,** proposes config instead of editing shared config, and runs that app's checks.
- **Before reporting,** each agent re-runs the relevant tools on its own folder, so it catches the findings its own new code adds.
- **An agent that starts helpers** reports only after every helper has finished.
- **No agent commits.** You commit, once all have reported.

Before you commit or measure, check that no agent is still running, and that `git diff --stat` stays inside the agents' folders.

**Keep the evidence** for "behaviour unchanged", such as rendered-HTML diffs or golden outputs, for the PR body. One commit per app keeps a round reviewable.

## Image vulnerabilities

They describe the published image, so cq's numbers move only after a deploy. Check a Dockerfile fix on a local build that is never pushed, with `trivy image` before and after, and report both counts.

## A repo whose own code produces findings or metrics

That means cq itself, or another analysis tool. A refactor of that code must keep its output identical:
- record the output on stored snapshots before the change, and compare after;
- better, commit a golden fixture with its expected output, so CI holds it.
