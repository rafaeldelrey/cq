"""Cognitive complexity per function (complexipy): how hard a function is to read, not to test.

Cyclomatic complexity counts paths; cognitive complexity also charges for nesting, so a flat
``match`` with ten arms scores low and three nested loops score high.
"""

import json
from statistics import mean

from cq.context import Context
from cq.proc import own, read_report, run

KINDS = ("python",)


def collect(ctx: Context) -> dict:
    """complexipy over the production paths, test directories excluded."""
    out_file = ctx.raw_dir / "complexipy.json"
    paths = [str(p) for p in ctx.sources]
    run(
        [
            own("complexipy"),
            *paths,
            "--exclude",
            "tests",
            "--output-format",
            "json",
            "--output",
            str(out_file),
            "-q",
            "-i",
        ],
        ctx.checkout,
        ok_codes=(0, 1),  # 1: some function is over complexipy's limit — still a measurement
    )
    rows = json.loads(read_report(out_file, "complexipy"))
    limit = ctx.settings.cognitive_limit
    scores = [r["complexity"] for r in rows]
    worst = sorted(rows, key=lambda r: -r["complexity"])[:15]
    return {
        "functions": len(rows),
        "mean": round(mean(scores), 2) if scores else 0.0,
        "max": max(scores, default=0),
        "over_limit": sum(1 for s in scores if s > limit),
        # Every function over the limit and its score: the gate's ratchet compares them by name.
        "over_limit_items": {
            f"{ctx.rel(r['path'], ctx.checkout)}::{r['function_name'].replace('::', '.')}": r["complexity"]
            for r in rows
            if r["complexity"] > limit
        },
        "limit": limit,
        "worst": [
            {
                "function": f"{ctx.rel(r['path'], ctx.checkout)}::{r['function_name'].replace('::', '.')}",
                "score": r["complexity"],
            }
            for r in worst
        ],
    }
