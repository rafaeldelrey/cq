"""Hotspots: production files that are both complex and changed often (churn × complexity).

A complex file nobody touches costs little; a simple file that changes every week costs little.
A complex file that changes every week is where bugs and slow changes come from, so it is the
best place to refactor first (Adam Tornhill's hotspot analysis, what CodeScene sells).

Churn is the number of commits that touched the file in the ``HOTSPOT_DAYS`` before the measured
commit's date (git log of the checkout, so a backfilled commit is judged on its own past).
Complexity is the sum of the file's functions' cyclomatic complexity, measured as the gate does.
"""

from collections import Counter
from datetime import datetime, timedelta

from cq.collectors.complexity import measure
from cq.context import Context
from cq.proc import run

KINDS = ("python",)

HOTSPOT_DAYS = 90
MIN_CHURN = 5  # a file needs at least this many commits in the window to count as a hotspot


def churn(ctx: Context, days: int = HOTSPOT_DAYS) -> Counter[str]:
    """Commits per repo-relative file in the ``days`` before HEAD's commit date."""
    head_date = datetime.fromisoformat(run(["git", "show", "-s", "--format=%cI", "HEAD"], ctx.checkout).strip())
    since = (head_date - timedelta(days=days)).isoformat()
    roots = [ctx.rel(p) for p in ctx.sources]
    out = run(
        ["git", "log", "HEAD", f"--since={since}", "--format=", "--name-only", "--no-renames", "--", *roots],
        ctx.checkout,
    )
    return Counter(line for line in out.splitlines() if line.endswith(".py") and "/tests/" not in line)


def collect(ctx: Context) -> dict:
    """Rank files by churn × summed complexity; count those over MIN_CHURN with a function over the limit."""
    per_function = measure(ctx)
    total_cc: Counter[str] = Counter()
    worst_cc: dict[str, int] = {}
    for key, cc in per_function.items():
        file = key.split("::", 1)[0]
        total_cc[file] += cc
        worst_cc[file] = max(cc, worst_cc.get(file, 0))
    commits = churn(ctx)
    limit = ctx.settings.complexity_limit
    rows = [
        {
            "file": file,
            "commits": commits[file],
            "complexity": total_cc[file],
            "worst_cc": worst_cc[file],
            "score": commits[file] * total_cc[file],
        }
        for file in total_cc
        if commits[file]
    ]
    rows.sort(key=lambda r: -r["score"])
    hot = [r for r in rows if r["commits"] >= MIN_CHURN and r["worst_cc"] > limit]
    return {
        "window_days": HOTSPOT_DAYS,
        "files_changed": len(rows),
        "hotspots": len(hot),
        "top_score": rows[0]["score"] if rows else 0,
        "items": ctx.cap(rows, 20),
    }
