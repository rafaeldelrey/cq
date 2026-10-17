"""Fill the history with snapshots of past commits, so the trend starts before the first run.

Usage (from code_quality/):
    uv run backfill.py --since 2026-06-01 --every 14     # one commit on main every 14 days
    uv run backfill.py --rev abc1234 --rev def5678       # specific commits
    uv run backfill.py --since 2026-06-01 --dry-run      # list the commits only

Each commit is measured in a temporary worktree with today's tools and config, so old and new
points are comparable. A commit that already has a history file is skipped unless --force.
An app that did not exist yet, or a tool its old setup cannot run, is recorded as an error.
"""

import argparse
import sys

from cq.config import HISTORY_DIR, load_settings
from cq.runner import cleanup_worktrees, first_parent_commits, git, save, snapshot


def main() -> int:
    """Pick the commits, then measure each one."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", help="ISO date of the first step")
    parser.add_argument("--every", type=int, default=14, help="days between steps (default 14)")
    parser.add_argument("--rev", action="append", default=[], help="a specific commit (repeatable)")
    parser.add_argument("--force", action="store_true", help="re-measure commits already in history/")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.since and not args.rev:
        parser.error("give --since or --rev")

    settings = load_settings()
    shas = [git("rev-parse", r) for r in args.rev]
    if args.since:
        shas += first_parent_commits(args.since, args.every)
    done = {p.stem.split("_", 1)[1].removesuffix("-dirty") for p in HISTORY_DIR.glob("*.json")}
    todo = [s for s in dict.fromkeys(shas) if args.force or s[:7] not in done]
    for sha in todo:
        print(git("show", "-s", "--format=%h %cs %s", sha), file=sys.stderr)
    if args.dry_run or not todo:
        print(f"{len(todo)} commit(s) to measure.", file=sys.stderr)
        return 0
    cleanup_worktrees()
    for i, sha in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] measuring {sha[:7]}", file=sys.stderr)
        snap = snapshot(settings, rev=sha)
        # A commit from before any app existed says nothing about quality: keep it out of the trend.
        for path in save(snap, to_history=bool(snap["targets"])):
            print(f"wrote {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
