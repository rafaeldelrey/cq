# Following a PR to ready

A PR is **ready** when all three hold:
- CI is green on its head commit;
- the head has been reviewed: by Codex where the repo has it, otherwise by a fresh-context Claude review;
- no review thread is open.

A repo with a merge gate and a status helper decides readiness with its own answer, so use it when there is one. A helper copied from another repo can fall behind that repo's gate version.

## Open it

1. Bump the version and add the changelog entry, per the repo's rules.
2. Write the body: the report's numbers, the decision, the config ledger and every review outcome, ending with the session's attribution line.
3. Push, then:

   ```bash
   gh pr create --base main --head <branch> --title "<summary>" --body-file <body file>
   ```

## The cycle, once per head

1. **CI:** `gh pr checks <n> --watch --fail-fast`.
   - On a failure, read the log (`gh run view <run> --log-failed`), fix, commit and push.
   - Then start the cycle again for the new head.
2. **Ask for the review,** once per head. Where the repo has Codex, write this comment to a file and post it with `gh pr comment <n> --body-file <file>`:

   ```
   @codex review

   <!-- merge-gate head: <full head sha> -->
   ```

   Where the repo has no Codex, or Codex answers that it is out of quota, a fresh-context Claude subagent reviews the head instead. Post its review on the head with `gh pr review`.
3. **Wait for the answer,** in the background, checking every 2 minutes for up to 30. Codex takes 3–6. These count as Codex's answer:
   - a 👍 on the request;
   - a review on the head commit;
   - a comment naming the head ("Reviewed commit: <sha>").

   Codex's "Codex Review Summary" comment names the head while its review is still running. It counts only once its hidden `codex-review-state` JSON shows `code_review.status` for this head as something other than `running`. A review Codex ran on its own, for example when the PR opened, doesn't count.
4. **Answer every unresolved thread:**
   - On the loop's path, or against the repo's conventions: fix it, and reply "Fixed in `<sha7>`. <what changed>".
   - Off-path, or something the repo's review rules leave alone: reply "Declined: <reason>".
   - A late duplicate of an earlier fix: reply "Already fixed in `<sha7>`".

   Resolve each thread once answered (GraphQL `resolveReviewThread`). Never write `@codex` in a reply. Push all the cycle's fixes together, then start again at 1.

## Stop and ask the user instead when

- the reviewer finds on-path problems in a third cycle in a row;
- Codex stays silent through two 30-minute waits;
- CI fails for a reason the branch didn't cause, such as a runner, a secret, or a bug in the gate itself;
- the repo's rules say the user lands this kind of PR by hand.
