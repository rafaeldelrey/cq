"""Fill the history with snapshots of past commits, so the trend starts before the first run.

Each commit is measured in a temporary worktree with today's tools and config, so old and new
points are comparable. A commit that already has a history file is skipped unless forced. An app
that did not exist yet, or a tool its old setup cannot run, is recorded as an error.
"""

from cq.config import Settings
from cq.console import note
from cq.runner import cleanup_worktrees, first_parent_commits, git, history_sha, save, snapshot


def commits_to_measure(
    settings: Settings, revs: list[str], since: str | None, every: int, *, force: bool, branch: str = "HEAD"
) -> list[str]:
    """The commits asked for (``revs``, and ``branch``'s first parents from ``since``), minus those in history/."""
    root = settings.repo_root
    shas = [git("rev-parse", r, cwd=root) for r in revs]
    if since:
        shas += first_parent_commits(since, every, root, branch)
    # A -dirty snapshot measured uncommitted changes, so it doesn't stand for the commit itself.
    done = {history_sha(p) for p in settings.history_dir.glob("*.json") if not p.stem.endswith("-dirty")}
    return [s for s in dict.fromkeys(shas) if force or s[:7] not in done]


def backfill(settings: Settings, todo: list[str]) -> None:
    """Measure each commit; a commit from before any app existed stays out of the trend."""
    cleanup_worktrees(settings)
    for i, sha in enumerate(todo, 1):
        note(f"[{i}/{len(todo)}] measuring {sha[:7]}")
        snap = snapshot(settings, rev=sha)
        for path in save(snap, settings, to_history=bool(snap["targets"])):
            note(f"wrote {path}")
