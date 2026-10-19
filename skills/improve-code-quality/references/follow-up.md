# Following the PR to ready

**Ready** means three things:
- CI is green on the PR's head commit;
- Codex has reviewed that head;
- no review thread is open.

In a repo with a merge-gate hook and a status helper (for example `ship_pr.py status`), the gate's own answer is the test.

## Open it

Bump the version and add the changelog entry per the repo's rules. Write the body with the report's numbers, the decision, the config ledger and every review outcome, and end it with the session's attribution line. Write the body to a file, push, then:

```bash
gh pr create --base main --head <branch> --title "<summary>" --body-file <file>
```

## The cycle, once per head

1. **CI:** `gh pr checks <n> --watch --fail-fast`. On a failure, read the log (`gh run view <run> --log-failed`), fix, commit and push, then start the cycle again for the new head.
2. **Ask Codex**, once per head. Write this comment to a file and post it with `gh pr comment <n> --body-file <file>`:

   ```
   @codex review

   <!-- merge-gate head: <full head sha> -->
   ```

   A review Codex ran on its own, when the PR opened for example, doesn't count.
3. **Wait for its answer:** a 👍 reaction on that comment, or a review or comment that names this head ("Reviewed commit: <sha>"). Check every 2 minutes, for up to 30 minutes. Codex takes 3–6. Run the wait in the background so the session stays free.
   - **Codex also posts a "Codex Review Summary" status comment** that names the head while the review is still running. Its hidden `<!-- codex-review-state:v1 {…} -->` JSON carries `code_review.status`. That comment is an answer only once the status for this head is no longer `running`.
4. **Handle each unresolved thread:**
   - On the loop's path, or against the repo's conventions: fix it, and reply "Fixed in `<sha7>`. <what changed>".
   - Off-path, or something the repo's review rules say not to flag: reply "Declined: <reason>".
   - A late duplicate of an earlier fix: reply "Already fixed in `<sha7>`".

   Resolve each thread after replying (GraphQL `resolveReviewThread`). Never write `@codex` in a reply. Push all the round's fixes together, then start again at step 1.
5. **Ready:** CI green, Codex's answer names the head, and no thread is open.

## When to stop and ask instead

- Codex finds on-path problems in a third cycle in a row.
- Codex is silent through two 30-minute waits. If it says it is out of quota, a fresh-context Claude review stands in: post it as a PR review that names the head.
- CI fails for a reason the branch didn't cause (a runner or a secret).
- The repo's rules say the user lands this kind of PR by hand.

## What never happens

Landing the PR is the user's call. The loop ends at ready and asks.
